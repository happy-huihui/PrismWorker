from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import AIMessage, ToolMessage

ModelRequest = types.ModelRequest

"""
    子代理中间件（subagent_middlewares）——委托账本与派发上限护栏。

    DelegationLedgerMiddleware（委托账本）：
      用 ThreadState.delegations 记录每一次子代理派发的完整生命周期：
        - abefore_model：扫最新 AIMessage 里对 task 工具的调用（tool_calls），
          以 tool_call_id 为 id 写入一条 status="in_progress" 的账本条目
          （merge_delegations 同 id 幂等合并，重复扫描不会重复记账）；
        - aafter_model：扫本轮新增的 ToolMessage（name == "task"），解析其
          结果（内含子代理状态/结果摘要），把对应条目更新为终止状态。
      status 取值：in_progress（进行中，非终止态）与 SUBAGENT_STATUS_VALUES
      （completed / failed / cancelled / timed_out / polling_timed_out）。

    SubagentLimitMiddleware（派发上限）：
      每次运行（run）允许的子代理总调用数上限 max_total_per_run（默认 10）。
      精简单线程计数：统计 state.delegations 里已进入终止态的条数，达到上限后
      把 task 工具从本次模型调用的工具列表里剔除，并注入系统提示说明已达上限
      （跨轮次由 checkpoint 持久化，近似“本次运行上限”语义）。
"""

logger = logging.getLogger(__name__)

_TASK_TOOL_NAME = "task"

_IN_PROGRESS_STATUS = "in_progress"



class DelegationLedgerMiddleware(AgentMiddleware):
    """委托账本中间件：记录每次子代理派发的生命周期进出账。"""

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：把待派发的 task 工具调用入账（in_progress）。"""
        messages = (state or {}).get("messages") or []
        latest_ai = _latest_ai_message(messages)
        calls = (latest_ai.tool_calls if latest_ai is not None else None) or []
        task_calls = [call for call in calls if str(call.get("name") or "") == _TASK_TOOL_NAME]
        if not task_calls:
            return None

        thread_id, run_id = _resolve_run_context(runtime)

        now = time.time()
        entries = []
        for call in task_calls:
            call_id = str(call.get("id") or "")
            args = call.get("args") or {}
            description = _extract_description(args)
            entry: dict[str, Any] = {
                "id": call_id,
                "description": description,
                "subagent_type": "general",
                "status": _IN_PROGRESS_STATUS,
                "created_at": _format_ts(now),
            }
            if thread_id:
                entry["run_id"] = f"{thread_id}:{run_id or 'pending'}"
            entries.append(entry)
        return {"delegations": entries}

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：把本轮新增的 task 工具结果结账（终止状态）。"""
        messages = (state or {}).get("messages") or []
        task_results = [
            m for m in messages
            if m is not None and getattr(m, "type", "") == "tool"
            and getattr(m, "name", "") == _TASK_TOOL_NAME
        ]
        if not task_results:
            return None

        entries = []
        prints: list[str] = []
        for result in task_results:
            tool_call_id = str(getattr(result, "tool_call_id", "") or "")
            parsed, status = _parse_task_result(result)
            entries.append({
                "id": tool_call_id,
                "status": status,
                "result_brief": parsed.get("result_brief", ""),
            })
            prints.append(f"子代理委托结束（{status}）")
        return {"delegations": entries, "prints": prints}



class SubagentLimitMiddleware(AgentMiddleware):
    """派发上限中间件：达到单次运行上限后禁止继续调用 task 工具。"""

    def __init__(self, *, max_total_per_run: int = 10) -> None:
        """初始化；max_total_per_run 为每次运行允许的子代理调用总数上限。"""
        self._max_total_per_run = max(1, max_total_per_run)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：已达上限则移除 task 工具并提示。"""
        state = request.state or {}
        delegations = state.get("delegations") or []
        terminal_count = sum(
            1 for entry in delegations
            if isinstance(entry, dict) and entry.get("status") not in (_IN_PROGRESS_STATUS, None)
        )
        if terminal_count < self._max_total_per_run:
            return await handler(request)
        tools = request.tools
        if tools:
            kept = [tool for tool in tools if _tool_name(tool) != _TASK_TOOL_NAME]
            if len(kept) != len(tools):
                tools = kept
            else:
                tools = None
        prompt_text = (
            "注意：本次运行已达子代理派发上限"
            f"（{self._max_total_per_run} 次）。请直接基于已有结果继续作答，"
            "不要再调用子代理。"
        )
        system_message = request.system_message
        if system_message is None:
            from langchain_core.messages import SystemMessage

            system_message = SystemMessage(content=prompt_text)
        else:
            system_message = system_message.__class__(
                content=f"{prompt_text}\n\n{system_message.text}" if system_message.text else prompt_text
            )
        return await handler(request.override(tools=tools, system_message=system_message))



def _latest_ai_message(messages: list[Any]) -> AIMessage | None:
    """从后往前找最后一条 AIMessage。"""
    for message in reversed(messages):
        if message is None:
            continue
        if getattr(message, "type", "") == "ai":
            return message
    return None


def _resolve_run_context(runtime: Any) -> tuple[str | None, str | None]:
    """从 runtime（context / config）尽力解析 thread_id 与 run_id。"""
    context = getattr(runtime, "context", None) or {}
    thread_id = context.get("thread_id")
    run_id = context.get("run_id")
    if thread_id:
        return thread_id, run_id
    config = getattr(runtime, "config", None) or {}
    configurable = config.get("configurable") or {}
    return configurable.get("thread_id"), configurable.get("run_id")


def _extract_description(args: dict[str, Any]) -> str:
    """从 task 工具参数里提取任务描述（供账本展示）。"""
    for key in ("description", "task", "task_description"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    try:
        import json

        return json.dumps(args, ensure_ascii=False)[:200]
    except Exception:  # noqa: BLE001
        return str(args)[:200]


def _parse_task_result(result: ToolMessage) -> tuple[dict[str, str], str]:
    """解析 task 工具结果，返回 (摘要信息, 终止状态)。

    兼容两种形态：
      - 内容为 JSON dict（含 status / error / result）→ 结构化解析；
      - 普通文本 → 字符串摘要，状态按是否有 error 字样判定。
    解析失败的兜底状态统一 failed，避免账本悬挂在 in_progress。
    """
    content = result.content
    if isinstance(content, str) and content.strip().startswith(("{", "[")):
        try:
            import json

            parsed = json.loads(content)
            if isinstance(parsed, dict):
                status = str(parsed.get("status") or "")
                if status not in _subagent_status_values():
                    status = "failed" if parsed.get("error") else "completed"
                brief = str(
                    parsed.get("result_brief")
                    or parsed.get("result")
                    or parsed.get("error")
                    or ""
                )
                return {"result_brief": brief[:500]}, status
        except (ValueError, TypeError):
            pass
    if isinstance(content, str):
        text = content.strip()
        status = "failed" if "error" in text.lower() else "completed"
        return {"result_brief": text[:500]}, status
    return {"result_brief": "（无法解析的子代理结果）"}, "failed"


def _subagent_status_values() -> tuple[str, ...]:
    """子代理终止状态的合法值（运行时导入避免循环）。"""
    from harness.subagents.status_contract import SUBAGENT_STATUS_VALUES

    return tuple(SUBAGENT_STATUS_VALUES)


def _format_ts(value: float) -> str:
    """时间戳 → ISO 字符串。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(value))


def _tool_name(tool: Any) -> str:
    """从（BaseTool | dict schema）里安全提取工具名。"""
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    name = getattr(tool, "name", None)
    return str(name) if name else ""