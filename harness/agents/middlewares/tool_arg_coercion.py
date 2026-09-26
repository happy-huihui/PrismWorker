from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types

ToolCallRequest = types.ToolCallRequest


"""
    工具参数容错中间件（tool_arg_coercion）——修「嵌套参数被模型写成 JSON 字符串」。

    背景（实测 2026-09-25，MiMo mimo-v2.6-pro）：
      ask_clarification 的 `fields` 是嵌套数组，模型却把它**双编码成 JSON 字符串**
      （形如 `'[{"name": "style", ...}]'`）。langgraph 的 ToolNode 在真正执行工具前
      会用 Pydantic 校验参数，字符串不是 list → 抛 ToolInvocationError → 兜成一条
      `status="error"` 的 ToolMessage。表现：思考链里该工具步骤标红「失败」，
      且这条错误消息会随 checkpoint 进对话历史，污染下一轮上下文。

    做法：在工具执行**之前**，只对「schema 不接受字符串、但字符串是合法 JSON 且
      解析后 schema 接受」的参数做还原。判定完全交给 Pydantic（TypeAdapter），
      不做启发式猜测——这样 `content: str` 这类正常字符串参数不会被误伤
      （比如 write_file 写一段看起来像 JSON 的文本）。

    为什么不用「放宽工具签名」：把 `fields: list[...]` 改成 `list[...] | str` 会改变
      暴露给模型的 JSON schema，等于默许它继续传字符串。这里保持契约严格、
      只在运行时容错。

    对外暴露：
        - ToolArgCoercionMiddleware  工具参数容错中间件
"""

logger = logging.getLogger(__name__)

# 只对形如数组/对象的字符串尝试解析（普通文本直接跳过，省一次 parse）
_JSON_HINT_PREFIXES = ("[", "{")

# TypeAdapter 缓存：(工具参数模型, 字段名) -> TypeAdapter（构造有开销，按字段复用）
_adapter_cache: dict[tuple[Any, str], Any] = {}


class ToolArgCoercionMiddleware(AgentMiddleware):
    """把「schema 要数组/对象、模型却给 JSON 字符串」的参数还原成原类型。"""

    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装工具执行：先做参数容错，再交给下游（事件下发 / 真正执行）。"""
        fixed_call = _coerce_tool_call(request)
        # 只有确实改动过才换请求对象，避免无谓的对象重建
        if fixed_call is not None:
            request = request.override(tool_call=fixed_call)
        return await handler(request)


def _coerce_tool_call(request: ToolCallRequest[Any]) -> dict[str, Any] | None:
    """修正工具调用参数；无需修正时返回 None（调用方据此跳过 override）。

    参数：
        request: 工具调用请求（含 tool_call 与 tool）

    返回：
        修正后的 tool_call 字典；无需修正返回 None
    """
    call = getattr(request, "tool_call", None) or {}
    args = call.get("args")
    # 参数不是非空 dict 就没什么可修的
    if not isinstance(args, dict) or not args:
        return None

    schema = _args_schema(request)
    if schema is None:
        return None

    # 逐参数尝试还原，记录是否有实际改动
    coerced: dict[str, Any] = {}
    changed = False
    for key, value in args.items():
        new_value = _coerce_value(schema, key, value)
        if new_value is not value:
            changed = True
        coerced[key] = new_value

    return {**call, "args": coerced} if changed else None


def _args_schema(request: ToolCallRequest[Any]) -> Any | None:
    """取工具的 pydantic 参数模型；取不到（None/非模型）返回 None。"""
    tool = getattr(request, "tool", None)
    schema = getattr(tool, "args_schema", None)
    # 只认 pydantic 模型类（有 model_fields 才算）
    if isinstance(schema, type) and hasattr(schema, "model_fields"):
        return schema
    return None


def _coerce_value(schema: Any, key: str, value: Any) -> Any:
    """把单个参数从 JSON 字符串还原回数组/对象；不该还原时原样返回。

    参数：
        schema: 工具的 pydantic 参数模型
        key: 参数名
        value: 模型给的值

    返回：
        还原后的值，或原值（当它是合法字符串、解析失败、或还原后仍不合法）
    """
    # 非字符串、或不像 JSON 的字符串 → 不碰
    if not isinstance(value, str):
        return value
    if not value.lstrip().startswith(_JSON_HINT_PREFIXES):
        return value

    adapter = _adapter_for(schema, key)
    if adapter is None:
        return value

    # 字符串本身就是合法值 → 不动（这是保护 content 这类文本参数的关键一步）
    if _accepts(adapter, value):
        return value

    # 解析成 JSON 后能被 schema 接受 → 说明模型把嵌套参数双编码成了字符串
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    if not _accepts(adapter, parsed):
        return value

    logger.info(
        "工具参数 %s 由 JSON 字符串还原为 %s（模型双编码）",
        key,
        type(parsed).__name__,
    )
    return parsed


def _adapter_for(schema: Any, key: str) -> Any | None:
    """取某参数的 TypeAdapter（带缓存）；字段不存在或无法构造返回 None。"""
    field = getattr(schema, "model_fields", {}).get(key)
    if field is None:
        return None

    cache_key = (schema, key)
    adapter = _adapter_cache.get(cache_key)
    if adapter is not None:
        return adapter

    try:
        from pydantic import TypeAdapter

        adapter = TypeAdapter(field.annotation)
    except Exception:  # noqa: BLE001 —— 注解无法解析（前向引用等）则放弃该字段
        _adapter_cache[cache_key] = None
        return None

    _adapter_cache[cache_key] = adapter
    return adapter


def _accepts(adapter: Any, value: Any) -> bool:
    """该值能否通过 schema 校验（任何校验异常都视为「不接受」）。"""
    if adapter is None:
        return False
    try:
        adapter.validate_python(value)
        return True
    except Exception:  # noqa: BLE001 —— 校验失败是预期分支，不记日志
        return False
