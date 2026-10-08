from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import AIMessage, ToolMessage

from harness.observability import context, get_observability_logger
from harness.observability.cost import compute_cost
from harness.observability.span import (
    SPAN_TYPE_LLM,
    SPAN_TYPE_TOOL,
    Span,
    get_span_collector,
)

ModelRequest = types.ModelRequest
ToolCallRequest = types.ToolCallRequest

"""span 采集中间件（trace）——在模型/工具调用边界采集结构化 span。

    职责：包住模型调用（awrap_model_call）与工具调用（awrap_tool_call），每次调用产生一条 span 
          记录"这次调用发了什么、回了什么、花了多少、真实耗时、成败与错误""
          存放到单例 SpanCollector，run 收尾时落 spans 表。
        无 run 上下文时（测试/直连）直接透传，不产生 span、不干扰主流程。

    对外暴露：
        - SpanMiddleware   span 采集中间件
"""


class SpanMiddleware(AgentMiddleware):
    """在模型/工具调用边界采集 span 的中间件（只观测、不改变结果）。"""

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """模型调用：llm span 包住 handler，回填用量 / 耗时 / 错误。"""
        run_id = context.current_run_id()
        # 无 run 上下文（测试/直连）→ 不采 span，直接透传
        if not run_id:
            return await handler(request)
        span = _start_span(run_id, SPAN_TYPE_LLM, _llm_span_name(request))
        with context.bind_span(span.span_id):
            try:
                result = await handler(request)
            except Exception as exc:  # noqa: BLE001 —— 记录失败后原样抛出，交给错误中间件兜底
                get_span_collector().finish_span(span, error=_brief(exc))
                raise
        # 出向形态契约：handler 返回 ModelResponse/ExtendedModelResponse/AIMessage，
        # 统一从消息里取 usage 与模型名（兼容三形态，见 _extract_usage）
        usage = _extract_usage(result)
        # 富化 attributes：系统提示 / 输入消息 / 输出正文 / 工具调用 / finish_reason / 思考 token / 调用参数
        # 原文落库（观测中台详情页的「输入/输出/参数」页签据此展示真实链路）
        span.attributes = {
            "messages": _messages_to_list(
                getattr(request, "messages", None),
                system_message=getattr(request, "system_message", None),
            ),
            "output": _extract_output_text(result),
            "tool_calls": _extract_tool_calls(result),
            "finish_reason": _extract_finish_reason(result),
            "reasoning_tokens": usage["reasoning_tokens"],
            "params": _model_params(getattr(request, "model", None)),
        }
        # 按模型单价算本 span 成本（模型聚合 SUM(cost) 依赖它，run 级成本也由 span 汇总）
        span_cost = compute_cost(
            usage["model_name"], usage["input_tokens"], usage["output_tokens"]
        )
        get_span_collector().finish_span(
            span,
            model_name=usage["model_name"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            cost=span_cost,
        )
        # 结构化日志：模型调用完成（含用量/耗时，观测中台按 event 动词聚合）
        get_observability_logger("prism.llm").info(
            "llm_call_finished",
            model_name=usage["model_name"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            duration_ms=span.duration_ms,
        )
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """工具执行：tool span 包住 handler，记录参数 / 耗时 / 成败。"""
        run_id = context.current_run_id()
        if not run_id:
            return await handler(request)
        tool_call = getattr(request, "tool_call", None) or {}
        tool_name = str(tool_call.get("name") or "tool")
        call_id = str(tool_call.get("id") or "")
        span = _start_span(run_id, SPAN_TYPE_TOOL, f"tool.{tool_name}")
        # 参数原文进 attributes（用户 2026-09-28 拍板：不做脱敏、存原文）
        span.attributes = {"tool_call_id": call_id, "args": tool_call.get("args")}
        with context.bind_span(span.span_id):
            try:
                result = await handler(request)
            except Exception as exc:  # noqa: BLE001 —— 记录失败后原样抛出，交给错误中间件兜底
                err = _brief(exc)
                get_span_collector().finish_span(span, error=err)
                get_observability_logger("prism.tool").info(
                    "tool_call_failed", tool=tool_name, error=err, duration_ms=span.duration_ms
                )
                raise
        # 成败口径与 ToolProgress 一致：ToolMessage 且 status=error 才算失败
        ok = not (isinstance(result, ToolMessage) and getattr(result, "status", None) == "error")
        error = None if ok else _error_of(result)
        # 富化：工具执行结果原文进 attributes（观测中台「输出」页签展示）
        span.attributes["result"] = _tool_result_text(result)
        get_span_collector().finish_span(span, error=error)
        # 结构化日志：工具执行完成/失败
        get_observability_logger("prism.tool").info(
            "tool_call_failed" if error else "tool_call_finished",
            tool=tool_name,
            duration_ms=span.duration_ms,
            error=error,
        )
        return result


def _start_span(run_id: str, span_type: str, name: str) -> Span:
    """从观测上下文取归属并启动一条 span（父指针=当前 span）。"""
    return get_span_collector().start_span(
        run_id=run_id,
        thread_id=context.current_thread_id(),
        user_id=context.current_user_id(),
        span_type=span_type,
        name=name,
        trace_id=context.current_trace_id(),
        # 父指针取当前正在执行的 span（嵌套调用时自然构成树）；无则为 run 根
        parent_span_id=context.current_span_id() or None,
    )


def _llm_span_name(request: ModelRequest[Any]) -> str:
    """拼 llm span 名字；取模型实例的 model_name（如 mimo-v2.6-pro），取不到退化为 llm.call。"""
    model = getattr(request, "model", None)
    # 1.request.model 是模型实例（非字符串），优先取它的 model_name 属性
    raw = getattr(model, "model_name", None) if model is not None else None
    # 2.个别模型包装层把 model_name 实现成对象描述，拿到的是 repr 碎片
    #   （形如 "metadata={'lc_versions': ...}"，含 = { < 等符号）→ 视为没取到干净名
    if isinstance(raw, str) and raw and not _REPR_JUNK_RE.search(raw) and len(raw) <= 64:
        return f"llm.{raw}"
    # 3.兜底：再试 model.model（字符串模型 id），仍取不到退化为 llm.call
    fallback = getattr(model, "model", None) if model is not None else None
    if isinstance(fallback, str) and fallback and not _REPR_JUNK_RE.search(fallback):
        return f"llm.{fallback}"
    return "llm.call"


# repr 碎片特征：干净模型名（mimo-v2.6-pro）不会出现 = { } < > 等符号
_REPR_JUNK_RE = re.compile(r"[={}<>{}\[\]\n]")


def _brief(exc: Exception) -> str:
    """把异常压缩成一行摘要（截断，防超长刷屏/落库膨胀）。"""
    cause = exc.__cause__ or exc
    text = str(cause).strip() or type(cause).__name__
    first_line = text.splitlines()[0] if text else type(exc).__name__
    return first_line[:300]


def _error_of(result: Any) -> str | None:
    """失败结果里取一行错误摘要（成功返回 None）。"""
    if isinstance(result, ToolMessage) and getattr(result, "status", None) == "error":
        return str(result.content or "")
    return None


def _iter_messages(result: Any):
    """按 handler 返回三形态递归产出 AIMessage（懒生成器）。

    三形态：ExtendedModelResponse(.model_response) / ModelResponse(.result) /
    AIMessage；list/tuple 逐项递归。
    """
    if isinstance(result, AIMessage):
        yield result
    elif isinstance(result, (list, tuple)):
        for item in result:
            yield from _iter_messages(item)
    elif hasattr(result, "model_response"):
        yield from _iter_messages(result.model_response)
    elif hasattr(result, "result"):
        yield from _iter_messages(result.result)


def _extract_usage(result: Any) -> dict[str, Any]:
    """从模型响应里提取 (input/output/total/reasoning token, model_name)。

    参数：
        result: awrap_model_call 的 handler 返回值（三形态之一）

    返回：
        {"input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "model_name"}；
        取不到用量时 token 四项为 0、model_name 为 None
    """
    for message in _iter_messages(result):
        usage = getattr(message, "usage_metadata", None) or {}
        if not isinstance(usage, dict):
            continue
        # 兼容多种键名（input/prompt、output/completion）
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens) or 0)
        # 思考 token：OpenAI 兼容放 completion_tokens_details.reasoning_tokens，部分供应商直接 reasoning_tokens
        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = int(
            details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0
        )
        # 三项全零视为没回用量，继续找下一条（可能有多条 AI 消息）
        if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
            continue
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
            "model_name": _model_name_of(message),
        }
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "model_name": None,
    }


def _model_name_of(message: Any) -> str | None:
    """从 AI 消息的 response_metadata 取模型名（取不到返回 None）。"""
    meta = getattr(message, "response_metadata", None) or {}
    if isinstance(meta, dict):
        return meta.get("model_name") or meta.get("model") or None
    return None


def _content_text(content: Any) -> str:
    """把消息 content 统一转字符串（兼容 str 与多模态块列表）。"""
    # 1.字符串原样返回
    if isinstance(content, str):
        return content
    # 2.块列表：只取 text 块拼起来（图片等非文本块跳过）
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    # 3.其余类型兜底 str
    return str(content)


def _messages_to_list(messages: Any, *, system_message: Any = None) -> list[dict[str, str]]:
    """把发给模型的完整消息列表转成 [{role, content}]（存原文）。

    system_message 单独收：langchain agents 的 ModelRequest 把系统提示放在
    system_message 字段而非 messages 列表里，不显式拼进来观测台就看不到 system。
    """
    out: list[dict[str, str]] = []
    # 1.系统提示排最前（存在且非空才收；兼容 SystemMessage 实例与裸字符串两种形态）
    if system_message is not None:
        sys_content = (
            system_message
            if isinstance(system_message, str)
            else _content_text(getattr(system_message, "content", ""))
        )
        if sys_content.strip():
            out.append({"role": "system", "content": sys_content})
    # 2.对话消息逐条收
    for message in messages or []:
        role = str(getattr(message, "type", None) or getattr(message, "role", "unknown"))
        out.append({"role": role, "content": _content_text(getattr(message, "content", ""))})
    return out


# 模型调用参数白名单：只收这些常见采样参数，避免把模型实例整个塞进 span
_PARAM_KEYS = (
    "temperature",
    "max_tokens",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "n",
    "stop",
    "timeout",
)


def _model_params(model: Any) -> dict[str, Any]:
    """从模型实例取调用参数（白名单字段，取不到就不放），落 span.attributes.params。"""
    params: dict[str, Any] = {}
    if model is None:
        return params
    for key in _PARAM_KEYS:
        value = getattr(model, key, None)
        # 1.数值直接收（temperature/max_tokens 等真实采样参数）
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            params[key] = value
        # 2.字符串防 repr 碎片混入（同 _llm_span_name 的脏名判定）
        elif isinstance(value, str) and value and not _REPR_JUNK_RE.search(value) and len(value) <= 200:
            params[key] = value
        # 3.列表只收纯标量项（如 stop 序列）
        elif isinstance(value, (list, tuple)) and value and all(
            isinstance(v, (str, int, float)) for v in value
        ):
            params[key] = list(value)
    return params


def _extract_output_text(result: Any) -> str:
    """从模型响应里取输出正文（第一条非空 AIMessage 的文本）。"""
    for message in _iter_messages(result):
        text = _content_text(getattr(message, "content", ""))
        if text:
            return text
    return ""


def _extract_tool_calls(result: Any) -> list[dict[str, Any]]:
    """从模型响应里取工具调用列表 [{name, args}]（无则空列表）。"""
    for message in _iter_messages(result):
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            return [
                {"name": str(c.get("name") or ""), "args": c.get("args")}
                for c in calls
                if isinstance(c, dict)
            ]
    return []


def _extract_finish_reason(result: Any) -> str | None:
    """从模型响应的 response_metadata 取 finish_reason（取不到 None）。"""
    for message in _iter_messages(result):
        meta = getattr(message, "response_metadata", None) or {}
        if isinstance(meta, dict) and meta.get("finish_reason"):
            return str(meta["finish_reason"])
    return None


def _tool_result_text(result: Any) -> str:
    """把工具返回结果转成可落库的文本（ToolMessage 取 content，其余 str 兜底截断）。"""
    if isinstance(result, ToolMessage):
        return _content_text(getattr(result, "content", ""))
    text = str(result)
    # 非 ToolMessage 兜底：截断防异常工具把整包塞进 span
    return text[:8000]
