from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import ToolMessage

ToolCallRequest = types.ToolCallRequest


"""
    工具进度中间件（tool_progress）——工具调用生命周期事件的思考链通道。

    设计（最小实现，替代 DeerFlow 的完整 tool_progress 组件）：
      事件通道 = 可注入的 event_sink 回调：每次工具调用开始 / 结束 /
        失败时，把结构化事件（event/tool/tool_call_id/args_preview…）推给
        外部消费方（运行层借此把思考链数据推给前端）。

    实现方式：awrap_tool_call 包住工具执行本体——这是唯一能在「工具真正开始
      跑之前」发事件的位置。旧版挂在 abefore_model / aafter_model 上扫描消息，
      等它看到 tool_calls 时工具其实早就执行完了，前端因此：① 工具步骤晚到
      一步，② duration 量的是「两轮模型调用之间的间隔」而不是工具耗时。
      现在 start/end 各自贴着执行边界，耗时真实、进度即时。

    不再写 prints：旧版额外写一行“即将调用工具 X…”/“工具 X 执行完成…”进思考链，
      与 tool_start/tool_end 事件完全重叠，前端只能靠正则去重、顺序也易错；
      工具步骤统一由事件通道承担，prints 只留给真正的人类可读进度。

    默认 event_sink=None 时事件收集到本实例的 events 列表（in-process
    in-list，供测试/调试读取）。
"""

logger = logging.getLogger(__name__)



class ToolProgressMiddleware(AgentMiddleware):
    """工具进度中间件：向 event_sink 推送工具生命周期事件（贴着执行边界）。"""

    def __init__(
        self,
        *,
        event_sink: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        max_args_preview: int = 400,
    ) -> None:
        """初始化；event_sink 为可注入的异步/同步事件回调（None → 收集到 events）。"""
        self._event_sink = event_sink
        self._max_args_preview = max_args_preview
        self.events: list[dict[str, Any]] = []


    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装工具执行：进入时发 start，返回/抛异常时发 end（带真实耗时与成败）。"""
        tool_call = getattr(request, "tool_call", None) or {}
        call_id = str(tool_call.get("id") or "")
        name = str(tool_call.get("name") or "")
        args = tool_call.get("args")
        started = time.monotonic()
        await self._emit({
            "event": "tool_start",
            "tool": name,
            "tool_call_id": call_id,
            # 结构化参数（供前端渲染「路径 / 命令 / 关键词」chip，不截断语义字段）
            "args": _safe_args(args),
            # 模型自填的一行动作标题（DeerFlow 同款：卡片标题首选它）
            "description": _describe(args),
            # 兼容字段：整包 JSON 预览（旧前端 / 落库回放仍可读）
            "args_preview": _preview_args(args, self._max_args_preview),
            "ts": time.time(),
        })
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001 —— 记录后原样抛出，交给错误中间件兜底
            await self._emit(_end_event(name, call_id, started, ok=False, error=str(exc)))
            raise
        await self._emit(
            _end_event(name, call_id, started, ok=_is_ok(result), error=_error_of(result))
        )
        return result


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



def _end_event(
    name: str, call_id: str, started: float, *, ok: bool, error: str | None
) -> dict[str, Any]:
    """构造一条 tool_end 事件（耗时按执行边界算，单位秒）。"""
    event: dict[str, Any] = {
        "event": "tool_end",
        "tool": name,
        "tool_call_id": call_id,
        "duration_seconds": round(time.monotonic() - started, 2),
        "ok": ok,
        "ts": time.time(),
    }
    if error:
        event["error"] = error[:200]
    return event


def _is_ok(result: Any) -> bool:
    """工具结果是否成功（ToolMessage.status == 'error' 视为失败）。"""
    return not (isinstance(result, ToolMessage) and getattr(result, "status", None) == "error")


def _error_of(result: Any) -> str | None:
    """失败结果里取一行错误摘要（成功返回 None）。"""
    if isinstance(result, ToolMessage) and getattr(result, "status", None) == "error":
        return str(result.content or "")
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


# 结构化 args 下发时的单值长度上限（避免把整个文件内容塞进事件）
_MAX_ARG_VALUE_CHARS = 2000
# 最多下发多少个参数键（防御异常工具定义）
_MAX_ARG_KEYS = 32


def _safe_args(args: Any) -> dict[str, Any]:
    """把工具参数整理成可安全下发/落库的 dict。

    参数：
        args: 模型的原始工具参数（应为 dict，异常时可能为 str/None）

    返回：
        结构化参数字典（超长字符串截断、非 JSON 可序列化值降级为 str）

    说明：前端要按 `path` / `command` / `query` 等语义键渲染 chip，
        所以不能只给一串被截断的 JSON 文本——那会把键名也截掉。
    """
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for index, (key, value) in enumerate(args.items()):
            if index >= _MAX_ARG_KEYS:
                break
            out[str(key)] = _safe_arg_value(value)
        return out
    if isinstance(args, str) and args.strip():
        return {"value": _truncate(args)}
    return {}


def _safe_arg_value(value: Any) -> Any:
    """单个参数值归一化：字符串截断、容器浅层清洗、其余降级为 str。"""
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_safe_arg_value(v) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k): _safe_arg_value(v) for k, v in list(value.items())[:20]}
    return _truncate(str(value))


def _truncate(text: str) -> str:
    """超长文本按上限截断（保留尾部省略号，便于前端识别未完整）。"""
    return text if len(text) <= _MAX_ARG_VALUE_CHARS else text[:_MAX_ARG_VALUE_CHARS] + "…"


def _describe(args: Any) -> str:
    """取模型自填的一行动作标题（DeerFlow 的 args.description）。

    参数：
        args: 模型的原始工具参数

    返回：
        标题字符串；模型没填则返回空串

    说明：DeerFlow 里思考链卡片的标题正是模型在 tool_calls.args 里自填的
        `description`（如「加载前端设计技能来创建足球网站」）。它比按工具名
        反查的中文动作名更贴合当下语境，所以优先采用。
    """
    if not isinstance(args, dict):
        return ""
    for key in ("description", "summary", "title", "reason"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""
