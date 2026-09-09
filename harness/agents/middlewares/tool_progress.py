from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import AIMessage

ToolCallRequest = types.ToolCallRequest


"""
    工具进度中间件（tool_progress）——工具调用生命周期事件的思考链通道。

    用户确认的方案（最小实现，替代 DeerFlow 的完整 tool_progress 组件）：
      1. 事件通道 = 可注入的 event_sink 回调：每次工具调用开始 / 结束 /
        失败时，把结构化事件（event/tool/tool_call_id/args_preview…）推给
        外部消费方（运行层借此把思考链数据推给前端）；
      2. 进度通道 = ThreadState.prints：同时把一行人类可读的中文进度消息
        追加进 prints（run 入口用 astream(values) 取增量推给前端）。

    实现方式：不塞在 wrap_tool_call（那里无法往 state 写 prints），而是
      - abefore_model：扫描最新 AIMessage 里的 tool_calls，逐个发 start 事件
        并追加"即将调用…"进度；
      - aafter_model：扫描本轮新增的 ToolMessage，逐个发 end 事件并追加
        "已完成…"进度。
    默认 event_sink=None 时事件收集到本实例的 events 列表（in-process
    in-list，供测试/调试读取），并始终同时写 prints，保证双通道都可用。
"""

logger = logging.getLogger(__name__)


class ToolProgressMiddleware(AgentMiddleware):
    """工具进度中间件：向 event_sink 与 prints 双通道推送工具生命周期。"""

    def __init__(
        self,
        *,
        event_sink: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        max_args_preview: int = 120,
    ) -> None:
        """初始化；event_sink 为可注入的异步/同步事件回调（None → 收集到 events）。"""
        self._event_sink = event_sink
        self._max_args_preview = max_args_preview
        self.events: list[dict[str, Any]] = []
        self._started_at: dict[str, float] = {}
        self._ended_ids: set[str] = set()


    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：扫描待执行的 tool_calls 并发出 start 事件与进度。"""
        messages = (state or {}).get("messages") or []
        latest_ai = _latest_ai_message(messages)
        tool_calls = (latest_ai.tool_calls if latest_ai is not None else None) or []
        if not tool_calls:
            return None

        prints: list[str] = []
        now = time.monotonic()
        for call in tool_calls:
            name = str(call.get("name") or "")
            call_id = str(call.get("id") or "")
            self._started_at[call_id] = now
            args_preview = _preview_args(call.get("args"), self._max_args_preview)
            event = {
                "event": "tool_start",
                "tool": name,
                "tool_call_id": call_id,
                "args_preview": args_preview,
                "ts": time.time(),
            }
            await self._emit(event)
            prints.append(f"即将调用工具 {name}…")

        return {"prints": prints}


    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：扫描本轮新增的工具结果并发出 end 事件与进度。"""
        messages = (state or {}).get("messages") or []
        tool_messages = [m for m in messages if m is not None and getattr(m, "type", "") == "tool"]
        if not tool_messages:
            return None

        prints: list[str] = []
        for tm in tool_messages:
            call_id = str(getattr(tm, "tool_call_id", "") or "")
            if call_id and call_id in self._ended_ids:
                continue
            if call_id:
                self._ended_ids.add(call_id)
            name = str(getattr(tm, "name", "") or "")
            duration = None
            started = self._started_at.pop(call_id, None)
            if started is not None:
                duration = round(time.monotonic() - started, 2)
            event: dict[str, Any] = {
                "event": "tool_end",
                "tool": name,
                "tool_call_id": call_id,
                "duration_seconds": duration,
                "ts": time.time(),
            }
            await self._emit(event)
            if duration is not None:
                prints.append(f"工具 {name} 执行完成（耗时 {duration}s）")
            else:
                prints.append(f"工具 {name} 执行完成")

        return {"prints": prints}


    async def _emit(self, event: dict[str, Any]) -> None:
        """把事件交给注入的 sink；sink 为空则收集到 self.events。"""
        if self._event_sink is not None:
            try:
                result = self._event_sink(event)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 —— 事件消费失败不阻断主流程
                logger.warning("tool progress event sink 调用失败: %s", exc)
            return
        self.events.append(event)



def _latest_ai_message(messages: list[Any]) -> AIMessage | None:
    """从后往前找最后一条 AIMessage（不含 summary 等内部消息）。"""
    for message in reversed(messages):
        if message is None:
            continue
        if getattr(message, "type", "") == "ai":
            return message
    return None


def _preview_args(args: Any, max_chars: int) -> str:
    """把工具参数压缩成一行预览文本（JSON 化 + 截断）。"""
    if not args:
        return ""
    try:
        import json

        text = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except Exception:  # noqa: BLE001 —— 序列化失败用普通字符串兜底
        text = str(args)
    return text if len(text) <= max_chars else text[:max_chars] + "…"