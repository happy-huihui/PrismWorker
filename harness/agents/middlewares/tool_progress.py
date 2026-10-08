from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import ToolMessage

ToolCallRequest = types.ToolCallRequest


"""工具进度中间件

    职责：在工具真正执行的边界上发 start / end / error 事件，供运行层推给前端
        - 事件通道 = 可注入的 event_sink 回调
        - 挂在 awrap_tool_call 上，耗时真实、进度即时
        - 不写 prints（与事件重叠，前端只能靠正则去重）

    对外暴露：
        - ToolProgressMiddleware
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
        # 1.取出这次调用的标识与参数（缺字段一律降级成空值，事件下发不能炸掉主流程）
        tool_call = getattr(request, "tool_call", None) or {}
        call_id = str(tool_call.get("id") or "")
        name = str(tool_call.get("name") or "")
        args = tool_call.get("args")
        started = time.monotonic()
        # 2.工具还没开始跑就发 tool_start：前端能立刻把这一步挂到思考链上
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
        # 3.执行工具本体；异常也要先补一条 tool_end 再抛出，交给错误中间件兜底
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001 —— 记录后原样抛出，交给错误中间件兜底
            await self._emit(_end_event(name, call_id, started, ok=False, error=str(exc)))
            raise
        # 4.正常返回：按结果状态补 tool_end（耗时量的是真实执行时间，不再是两轮调用间隔）
        await self._emit(
            _end_event(name, call_id, started, ok=_is_ok(result), error=_error_of(result))
        )
        return result


    async def _emit(self, event: dict[str, Any]) -> None:
        """把事件交给注入的 sink；sink 为空则收集到 self.events。"""
        # 1.有 sink 就交给 sink：同步/异步都兼容，消费失败只告警、绝不阻断工具执行
        if self._event_sink is not None:
            try:
                result = self._event_sink(event)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[misc]
            except Exception as exc:  # noqa: BLE001 —— 事件消费失败不阻断主流程
                logger.warning("tool progress event sink 调用失败: %s", exc)
            return
        # 2.没有 sink（测试/调试场景）就攒在本实例的 events 列表里
        self.events.append(event)



def _end_event(
    name: str, call_id: str, started: float, *, ok: bool, error: str | None
) -> dict[str, Any]:
    """构造一条 tool_end 事件（耗时按执行边界算，单位秒）。"""
    # 1.基础字段：耗时用单调时钟相减，不受系统时间被调整的影响
    event: dict[str, Any] = {
        "event": "tool_end",
        "tool": name,
        "tool_call_id": call_id,
        "duration_seconds": round(time.monotonic() - started, 2),
        "ok": ok,
        "ts": time.time(),
    }
    # 2.失败才带错误摘要，且只留前 200 字（事件要落库/下发，不能无限膨胀）
    if error:
        event["error"] = error[:200]
    return event


def _is_ok(result: Any) -> bool:
    """工具结果是否成功（ToolMessage.status == 'error' 视为失败）。"""
    # 只有「ToolMessage 且 status=error」才算失败；其它形态（含非 ToolMessage）一律视为成功
    return not (isinstance(result, ToolMessage) and getattr(result, "status", None) == "error")


def _error_of(result: Any) -> str | None:
    """失败结果里取一行错误摘要（成功返回 None）。"""
    # 失败才取 content 当摘要，成功返回 None（不往事件里塞无关正文）
    if isinstance(result, ToolMessage) and getattr(result, "status", None) == "error":
        return str(result.content or "")
    return None



def _preview_args(args: Any, max_chars: int) -> str:
    """把工具参数压缩成一行预览文本（JSON 化 + 截断）。"""
    # 1.空参数直接给空串
    if not args:
        return ""
    # 2.JSON 化：sort_keys 让同样的参数生成同样的文本（便于前端/落库做稳定比对）
    try:
        import json

        text = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except Exception:  # noqa: BLE001 —— 序列化失败用普通字符串兜底
        text = str(args)
    # 3.超预算截断；这只是「整包预览」兼容字段，不影响上面结构化下发的 args
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
    # 1.dict 形态：逐键归一化；键数超上限直接截断（防御异常工具定义撑爆事件）
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for index, (key, value) in enumerate(args.items()):
            if index >= _MAX_ARG_KEYS:
                break
            out[str(key)] = _safe_arg_value(value)
        return out
    # 2.纯字符串参数（模型把整个参数体写成了一段文本）：包成 value 键，前端仍能展示
    if isinstance(args, str) and args.strip():
        return {"value": _truncate(args)}
    # 3.其它形态（None / 空串）没有可下发的结构
    return {}


def _safe_arg_value(value: Any) -> Any:
    """单个参数值归一化：字符串截断、容器浅层清洗、其余降级为 str。"""
    # 分支一：字符串按单值上限截断
    if isinstance(value, str):
        return _truncate(value)
    # 分支二：标量与 None 原样保留（本身可 JSON 序列化，无需处理）
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # 分支三：列表/字典只做浅层清洗，各留前 20 项（防嵌套结构把事件撑爆）
    if isinstance(value, list):
        return [_safe_arg_value(v) for v in value[:20]]
    if isinstance(value, dict):
        return {str(k): _safe_arg_value(v) for k, v in list(value.items())[:20]}
    # 分支四：其余类型（对象等）降级成字符串，保证一定能下发/落库
    return _truncate(str(value))


def _truncate(text: str) -> str:
    """超长文本按上限截断（保留尾部省略号，便于前端识别未完整）。"""
    # 只有超长才截断并补省略号——省略号就是给前端的「内容不完整」信号
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
    # 1.非 dict 参数里没有可取的自述字段
    if not isinstance(args, dict):
        return ""
    # 2.按优先级找模型自填的一行标题（description 最常见，其余是兜底别名）
    for key in ("description", "summary", "title", "reason"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            # 3.命中即返回并截到 200 字（卡片标题不需要更长）
            return value.strip()[:200]
    return ""
