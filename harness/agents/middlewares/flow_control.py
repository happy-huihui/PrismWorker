from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage

ModelRequest = types.ModelRequest

"""
    流程控制中间件（flow_control）——三项最小护栏合集。

    LoopDetectionMiddleware（循环检测）：
      检测模型反复调用同一个工具 + 相同参数（签名指纹相同）。最近窗口中
      同一签名出现 ≥ min_repeat_times（默认 3）次即判为循环：awrap_model_call
      注入系统提示引导模型换方案；abefore_model 补一条中文进度消息。

    DanglingToolCallMiddleware（孤立工具调用）：
      最新 AIMessage 声明的 tool_calls 在下游没有对应 ToolMessage（说明这
      些调用要么被护栏拦截、要么与已执行的一轮不一致）。检测到时注入提示，
      提醒模型不要依赖未执行成功的调用。

    SystemMessageCoalescingMiddleware（系统消息合并）：
      多条 SystemMessage 会稀释指令强度。发现 >1 条 system 时把它们合并为
      排在最前的一条（同 id 覆盖 + RemoveMessage 删除其余），保持上下文
      干净。summary 消息（name == "summary"）不参与合并。

    已移除：SafetyFinishReasonMiddleware（“模型已完成本轮输出”进度行）——
      它唯一作用是往 prints 写一行内部信号，而“本轮结束”已由 run 终端事件
      （run_finished）承担，留在思考链里只是噪声。
"""

logger = logging.getLogger(__name__)

_DEFAULT_MIN_REPEAT = 3
_LOOP_WINDOW = 8
_DANGLING_HINT = (
    "提示：以下工具调用缺少对应的执行结果，不会生效：{names}。"
    "请不要依赖它们，重新发起完整调用或改用其它方法。"
)



class LoopDetectionMiddleware(AgentMiddleware):
    """循环检测中间件：重复工具调用达到阈值时提醒模型换方案。"""

    def __init__(self, *, min_repeat_times: int = _DEFAULT_MIN_REPEAT, window: int = _LOOP_WINDOW) -> None:
        """初始化；min_repeat_times 判定循环的重复次数，window 检测窗口大小。"""
        self._min_repeat = max(2, min_repeat_times)
        self._window = max(4, window)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：存在循环签名时注入系统提示。"""
        repeated = _find_repeated_signatures(request.state, self._window, self._min_repeat)
        if not repeated:
            return await handler(request)
        names = ", ".join(f"「{name}」" for name in repeated)
        loop_notice = (
            f"警告：检测到对工具 {names} 的重复调用（参数一致）。"
            "这可能是循环。请停止重复，分析已有结果并尝试完全不同的方法，"
            "或直接基于现有信息给出最终回答。"
        )
        system_message = request.system_message
        if system_message is None:
            system_message = SystemMessage(content=loop_notice)
        else:
            text = system_message.text
            system_message = system_message.__class__(
                content=f"{loop_notice}\n\n{text}" if text else loop_notice
            )
        return await handler(request.override(system_message=system_message))

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：重复签名写入中文进度消息。"""
        repeated = _find_repeated_signatures(state, self._window, self._min_repeat)
        if not repeated:
            return None
        names = "、".join(repeated)
        return {"prints": [f"⚠ 检测到重复调用（{names}），已提醒模型更换方案"]}



class DanglingToolCallMiddleware(AgentMiddleware):
    """孤立工具调用中间件：提醒模型哪些 tool_calls 没有对应执行结果。"""

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：最新 AI 消息存在孤立调用时注入提示。"""
        dangling = _find_dangling_calls(request.state)
        if not dangling:
            return await handler(request)
        names = ", ".join(f"「{name}」" for name in dangling)
        system_message = request.system_message
        hint = _DANGLING_HINT.format(names=names)
        if system_message is None:
            system_message = SystemMessage(content=hint)
        else:
            text = system_message.text
            system_message = system_message.__class__(
                content=f"{hint}\n\n{text}" if text else hint
            )
        return await handler(request.override(system_message=system_message))



class SystemMessageCoalescingMiddleware(AgentMiddleware):
    """系统消息合并中间件：把多条 system 消息合并为一条，强化指令优先级。"""

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：合并多余的系统消息。"""
        messages = (state or {}).get("messages") or []
        system_messages = [
            m for m in messages
            if m is not None and getattr(m, "type", "") == "system"
            and getattr(m, "name", None) != "summary"
        ]
        if len(system_messages) <= 1:
            return None

        first = system_messages[0]
        merged_text = "\n\n".join(_extract_text(m) for m in system_messages)
        new_first = SystemMessage(content=merged_text)
        new_first.id = first.id
        removes = [
            RemoveMessage(id=m.id)
            for m in system_messages[1:]
            if m.id is not None
        ]
        return {"messages": [new_first, *removes]}



def _tool_call_signature(call: dict[str, Any]) -> str:
    """把一条 tool_call 折叠成签名指纹（name + 确定性 JSON args）。"""
    try:
        args_text = json.dumps(call.get("args") or {}, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        args_text = str(call.get("args"))
    digest = hashlib.sha256(args_text.encode("utf-8")).hexdigest()[:8]
    return f"{call.get('name')}#{digest}"


def _recent_tool_call_records(state: Any, window: int) -> list[tuple[str, str]]:
    """从消息历史里提取最近 window 条 (name, 签名) 记录（时间正序）。"""
    records: list[tuple[str, str]] = []
    messages = (state or {}).get("messages") or []
    for message in messages:
        if message is None or getattr(message, "type", "") != "ai":
            continue
        for call in getattr(message, "tool_calls", None) or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            records.append((name, _tool_call_signature(call)))
    return records[-window:]


def _find_repeated_signatures(state: Any, window: int, min_repeat: int) -> list[str]:
    """返回在最近 window 次调用中重复 ≥ min_repeat 次的工具名（按次数倒序）。"""
    records = _recent_tool_call_records(state, window)
    if len(records) < min_repeat:
        return []
    counter = Counter(signature for _, signature in records)
    repeated_signatures = {
        signature for signature, count in counter.items() if count >= min_repeat
    }
    names: list[str] = []
    for name, signature in records:
        if signature in repeated_signatures and name not in names:
            names.append(name)
    return names


def _find_dangling_calls(state: Any) -> list[str]:
    """返回最新 AI 消息里没有对应 ToolMessage 的 tool_call 名列表。"""
    if state is None:
        return []
    messages = (state or {}).get("messages") or []
    executed_ids = {
        str(getattr(m, "tool_call_id", ""))
        for m in messages
        if m is not None and getattr(m, "type", "") == "tool"
    }
    latest_ai = None
    for message in reversed(messages):
        if message is not None and getattr(message, "type", "") == "ai":
            latest_ai = message
            break
    if latest_ai is None:
        return []
    dangling: list[str] = []
    for call in getattr(latest_ai, "tool_calls", None) or []:
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("id") or "")
        if call_id and call_id not in executed_ids:
            name = str(call.get("name") or "")
            if name and name not in dangling:
                dangling.append(name)
    return dangling


def _extract_text(message_or_response: Any) -> str:
    """从消息提取纯文本（兼容 str 与多模态块列表）。"""
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