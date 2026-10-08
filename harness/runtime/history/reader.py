from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from harness.runtime.serialization import strip_user_input_wrapper, to_role

"""会话历史读取

    职责：从 checkpoints.db 读出指定线程最新一轮 checkpoint 的对话原文，转成前端可渲染的精简结构
        - 跳过 tool 消息；带工具调用的 assistant 视为中间步骤一并跳过
        - user 消息剥离防注入包裹标记；无 checkpoint 返回空数组

    对外暴露：
        - get_message_history  读线程会话历史（[{role, content}]）
        - count_messages       线程当前消息条数
"""


async def get_message_history(
    user_id: str,
    thread_id: str,
    *,
    limit: int = 200,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """读取线程会话历史（精简结构），无历史返回空列表。

    参数：
        user_id: 用户
        thread_id: 线程
        limit: 最多返回条数（钳制到 1-500）
        db_path: checkpoint 库路径；缺省按全局配置解析（测试可注入临时库）

    返回：
        形如 [{role, content}, ...] 的消息列表

    实现要点：
        - 与 run 共用同一个 checkpoints.db（resolve_memory_paths 解析路径）
        - 只读连接 + AsyncSqliteSaver.aget_tuple 取最新 checkpoint
        - 库文件不存在 / 无 checkpoint → 空列表（不建库、不报错）
    """
    # 限制单次读取量，避免异常大 limit 拖垮读取
    limit = max(1, min(int(limit), 500))
    if db_path is None:
        from harness.memory.paths import resolve_memory_paths

        _, db_path = resolve_memory_paths(None)

    # 统一成 Path，下面要做 exists() 判断
    db_path = Path(db_path)
    # 库还不存在 = 从没跑过，直接空
    if not db_path.exists():
        return []

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # 短连接：读完即关（finally），不长期占着库
    conn = await aiosqlite.connect(str(db_path))
    try:
        # 借用 LangGraph 的 saver 读最新 checkpoint，保证与 run 写入端同格式
        saver = AsyncSqliteSaver(conn)
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            }
        }
        # 取最新 checkpoint，没有则空
        tup = await saver.aget_tuple(config)
        if tup is None:
            return []
        channel_values = tup.checkpoint.get("channel_values", {}) or {}
        # 展示历史 = 压缩归档（被摘要中间件移除的早期消息原文）+ 当前上下文。
        # messages 只服务模型上下文窗口，可能被 RemoveMessage 物理清理；
        # archived_messages 只增不减，保证用户可见的对话历史完整。
        archived = channel_values.get("archived_messages", []) or []
        current = channel_values.get("messages", []) or []
        messages = [*archived, *current]
        # 逐条转成 {role, content}，中间步骤（tool / 带调用的 assistant）在这里被过滤
        result: list[dict[str, Any]] = []
        # 只取最近 limit 条
        seq = messages[-limit:]
        for i, msg in enumerate(seq):
            mtype = to_role(getattr(msg, "type", ""))
            # tool 消息不进历史展示
            if mtype == "tool":
                continue
            # 带工具调用、或下一条是 tool 的 assistant，属中间步骤，跳过
            if mtype == "assistant":
                has_tool_calls = bool(getattr(msg, "tool_calls", None))
                nxt = seq[i + 1] if i + 1 < len(seq) else None
                nxt_type = to_role(getattr(nxt, "type", "")) if nxt is not None else ""
                if has_tool_calls or nxt_type == "tool":
                    continue
            content = getattr(msg, "content", "")
            # 多模态内容块拼成文本
            if isinstance(content, list):
                texts: list[str] = []
                for piece in content:
                    if isinstance(piece, str):
                        texts.append(piece)
                    elif isinstance(piece, dict) and piece.get("type") == "text":
                        texts.append(str(piece.get("text", "")))
                content = "\n".join(t for t in texts if t)
            content = str(content)
            # user 消息剥离防注入标记
            if mtype == "user":
                content = strip_user_input_wrapper(content)
            result.append({"role": mtype, "content": content})
        return result
    finally:
        await conn.close()


async def count_messages(user_id: str, thread_id: str, *, db_path: str | Path | None = None) -> int:
    """线程当前消息条数（供 UI 角标/调试；无历史返回 0）。"""
    # 复用历史读取的过滤规则，保证角标条数与历史列表一致
    history = await get_message_history(user_id, thread_id, limit=500, db_path=db_path)
    return len(history)


async def _main() -> None:
    """命令行自检：python -m harness.runtime.history.reader。"""
    import sys

    # 参数不足只打印用法，不报错退出
    if len(sys.argv) < 3:
        print("用法: python -m harness.runtime.history.reader <user_id> <thread_id>")
        return
    msgs = await get_message_history(sys.argv[1], sys.argv[2])
    print(f"共 {len(msgs)} 条消息:")
    for m in msgs:
        print(f"  [{m['role']}] {m['content'][:80]}")


if __name__ == "__main__":
    asyncio.run(_main())
