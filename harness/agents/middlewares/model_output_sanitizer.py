from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import replace
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import AIMessage

ModelRequest = types.ModelRequest
ModelResponse = types.ModelResponse
ExtendedModelResponse = types.ExtendedModelResponse

"""
    模型输出净化中间件（model_output_sanitizer）——模型调用边界的双向消毒。

    背景（2026-09-26 线上实锤）：MiMo 会把工具调用退化成正文文本
    `<tool_call><function=NAME .../>`，API 层解析失败后原文泄漏进 content，
    同时在流里留下一个幻影 tool_call（name=""、id=None）。后果链：
      ToolNode 构造错误 ToolMessage 时 pydantic 拒绝 tool_call_id=None
      → 兜底 ToolMessage(tool_call_id="") 入历史
      → 下轮请求体出现 `"id": null` → MiMo 400「`id` is null」→ run 终止。

    两个方向（洋葱最外层注册，入向最先跑、出向最后跑）：
      入向（历史→模型）：丢弃幻影 tool_call、补齐缺失 id、清扫孤儿 ToolMessage、
        剥离历史正文里的 `<tool_call>` 壳。只 rebuild 受影响的消息对象，
        不写回 checkpoint（非破坏）。**永不**给旧消息新增 tool_call——
        旧调用没有 ToolMessage 配对，会造出新的 400。
      出向（模型→state）：同样净化 tool_calls；壳剥离时可恢复调用意图——
        activate_skill 变体归一化回 `<activate_skill name="X" />` 标签协议
        （SkillActivationMiddleware 照常激活），真实工具名 + 可解析参数
        （XML 属性对 / JSON 体）→ 还原成真 tool_call（合成 id，本轮照常执行）。

    对外暴露：
        - ModelOutputSanitizerMiddleware  净化中间件
        - sanitize_tool_calls             纯函数：净化一条 AI 消息的 tool_calls
        - clean_tool_call_shells          纯函数：剥离正文 `<tool_call>` 壳
        - repair_history                  纯函数：入向历史修复（无需修时返回 None）
"""

logger = logging.getLogger(__name__)

# 正文里学过/见过的三种退化形态：<tool_call> 壳、<function=NAME ...> 片段、属性对
_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\s*tool_call\s*>(?P<inner>.*?)(?:<\s*/\s*tool_call\s*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_FUNCTION_TAG_RE = re.compile(
    r"<\s*function\s*=\s*(?P<name>[A-Za-z0-9_.\-]+)\s*(?P<attrs>[^>]*?)(?P<close>/?>)",
    re.DOTALL,
)
_ATTR_PAIR_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_\-]*)\s*=\s*(?:\"(?P<d>[^\"]*)\"|'(?P<s>[^']*)')"
)
# 标签协议的技能激活标签（归一化目标，与 SkillActivationMiddleware 的正则对齐）
_ACTIVATE_SKILL_TAG = '<activate_skill name="{name}" />'

# 合成 id 的前缀（OpenAI 风格，便于日志辨认是净化器补的）
_SYNTHETIC_ID_PREFIX = "call_sanitized_"


def sanitize_tool_calls(message: AIMessage) -> tuple[AIMessage, bool]:
    """净化一条 AI 消息的 tool_calls：丢幻影、补 id；无需修时原样返回。

    参数：
        message: 模型产出的 AI 消息

    返回：
        (消息, 是否有改动)；改动时返回 model_copy，绝不原地改入参
    """
    calls = getattr(message, "tool_calls", None) or []
    kept: list[dict[str, Any]] = []
    changed = False
    for call in calls:
        # 非字典条目是损坏数据，直接丢
        if not isinstance(call, dict):
            changed = True
            continue
        name = str(call.get("name") or "").strip()
        if not name:
            # 幻影调用（空 name）：不可执行，留着只会污染请求体
            changed = True
            continue
        patched = dict(call)
        if not str(patched.get("id") or "").strip():
            # 缺 id：不补的话下轮请求体出现 "id": null，MiMo 直接 400
            patched["id"] = f"{_SYNTHETIC_ID_PREFIX}{uuid.uuid4().hex[:24]}"
            patched["type"] = "tool_call"
            changed = True
        if not isinstance(patched.get("args"), dict):
            patched["args"] = {}
            changed = True
        kept.append(patched)
    if not changed:
        return message, False
    # invalid_tool_calls 一并清空：langchain_openai 序列化只用 parsed tool_calls
    return message.model_copy(update={"tool_calls": kept, "invalid_tool_calls": []}), True


def clean_tool_call_shells(
    text: str,
    *,
    tool_names: frozenset[str] | set[str] = frozenset(),
    recover: bool = False,
) -> tuple[str, bool, list[dict[str, Any]]]:
    """剥离正文里的 `<tool_call>` 退化壳；recover=True 时还原可解析的调用。

    参数：
        text: assistant 正文（仅处理字符串正文）
        tool_names: 当前可用的真实工具名集合（recover 还原时做白名单）
        recover: 是否把 `<function=NAME ...>` 还原成真调用 / 标签协议

    返回：
        (清洗后正文, 是否有改动, 还原出的 tool_call 列表)
    """
    if not text or "<tool_call" not in text.lower() and "<function=" not in text.lower():
        return text, False, []

    recovered: list[dict[str, Any]] = []
    changed = False

    def _rebuild_block(match: re.Match[str]) -> str:
        """重建一个 <tool_call> 块：去壳、恢复可解析调用、保留其余内文。"""
        nonlocal changed
        inner = match.group("inner")
        rebuilt = _rebuild_inner(
            inner, tool_names=tool_names, recovered=recovered, recover=recover
        )
        if rebuilt != inner:
            changed = True
        return rebuilt

    cleaned = _TOOL_CALL_BLOCK_RE.sub(_rebuild_block, text)
    if changed:
        # 块外残留的孤立标签（未配对的 <function=…> 等）一并扫掉，防再入历史
        cleaned = re.sub(r"<\s*function\s*=[^>]*?/?>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(r"<\s*/\s*function\s*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<\s*/?\s*tool_call\s*>", "", cleaned, flags=re.IGNORECASE)
    return cleaned, changed, recovered


def _rebuild_inner(
    inner: str,
    *,
    tool_names: frozenset[str] | set[str],
    recovered: list[dict[str, Any]],
    recover: bool,
) -> str:
    """重建块内文：识别 `<function=NAME ...>` 片段并按工具名分派处理。"""
    func = _FUNCTION_TAG_RE.search(inner)
    if func is None:
        # 纯壳（没有 function 片段）：去掉壳标签，保留其余内文（如 <todo> 块）
        return _strip_shell_markup(inner)

    name = func.group("name")
    attrs = _parse_attrs(func.group("attrs"))
    body_json = _extract_body_json(inner, func)

    if not recover:
        # 入向：只剥离、不恢复——旧消息缺 ToolMessage 配对，新增调用必炸
        return _remove_function_fragment(inner, func)

    if name in tool_names and (attrs or body_json is not None):
        # 真实工具：还原成可执行的 tool_call，从正文里移除整个片段
        args: dict[str, Any] = {**attrs, **(body_json or {})}
        recovered.append({"name": name, "args": args, "id": _new_call_id(), "type": "tool_call"})
        return _remove_function_fragment(inner, func)

    if name == "activate_skill" and attrs.get("name"):
        # 标签协议变体：归一化回 prompt 教的 <activate_skill name="X" />
        tag = _ACTIVATE_SKILL_TAG.format(name=str(attrs["name"]).strip())
        return (
            inner[: func.start()]
            + tag
            + inner[func.end() :]
        )

    # 未知/无法恢复：剥壳保底，内文照留（通常还有 <todo> 等要交给下游解析）
    return _strip_shell_markup(inner)


def _parse_attrs(raw: str) -> dict[str, str]:
    """解析 `key="value"` / `key='value'` 属性对（解析不出就跳过）。"""
    attrs: dict[str, str] = {}
    for key, d_val, s_val in _ATTR_PAIR_RE.findall(raw):
        value = d_val if d_val is not None else s_val
        if key and value is not None:
            attrs[key] = value
    return attrs


def _extract_body_json(inner: str, func: re.Match[str]) -> dict[str, Any] | None:
    """`<function=NAME>{...}</function>` 体式参数：取 JSON 体并解析。"""
    if func.group("close") != ">":
        return None
    close_idx = inner.lower().find("</function", func.end())
    if close_idx < 0:
        return None
    body = inner[func.end() : close_idx].strip()
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _remove_function_fragment(inner: str, func: re.Match[str]) -> str:
    """从块内文里移除整个 function 片段（自闭合=标签本身；体式=连 body 一起）。"""
    if func.group("close") == ">":
        close_idx = inner.lower().find("</function", func.end())
        if close_idx >= 0:
            close_end = inner.find(">", close_idx)
            return inner[: func.start()] + inner[(close_end + 1) if close_end >= 0 else len(inner) :]
    return inner[: func.start()] + inner[func.end() :]


def _strip_shell_markup(text: str) -> str:
    """只去 `<tool_call>` / `<function=` 壳标签，不动其余内文。"""
    text = re.sub(r"<\s*function\s*=[^>]*?/?>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\s*/\s*function\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/?\s*tool_call\s*>", "", text, flags=re.IGNORECASE)
    return text


def _new_call_id() -> str:
    """合成一个请求体合法的 tool_call id。"""
    return f"{_SYNTHETIC_ID_PREFIX}{uuid.uuid4().hex[:24]}"


def sanitize_ai_message(
    message: Any,
    *,
    tool_names: frozenset[str] | set[str] = frozenset(),
    recover: bool = False,
) -> tuple[Any, bool]:
    """净化单条消息：AI 消息做 tool_calls 净化 + 正文壳剥离；其余原样透传。

    参数：
        message: 任意 langchain 消息
        tool_names: 真实工具名集合（recover 时用）
        recover: 出向 True（可恢复调用意图）；入向恒 False

    返回：
        (消息, 是否有改动)
    """
    if not isinstance(message, AIMessage):
        return message, False
    current, changed = sanitize_tool_calls(message)
    content = current.content
    if isinstance(content, str):
        cleaned, text_changed, recovered = clean_tool_call_shells(
            content, tool_names=tool_names, recover=recover
        )
        if text_changed:
            current = current.model_copy(update={"content": cleaned})
            changed = True
        if recovered:
            calls = list(getattr(current, "tool_calls", None) or [])
            current = current.model_copy(update={"tool_calls": [*calls, *recovered]})
            changed = True
    return current, changed


def repair_history(messages: list[Any] | None) -> list[Any] | None:
    """入向历史修复：净幻影、补 id、扫孤儿 ToolMessage、剥正文壳。

    为什么入向不恢复调用：旧 AI 消息的 tool_calls 必须与已有 ToolMessage
    一一配对，凭空恢复只会造出「有调用无响应」的新 400。

    参数：
        messages: 将发给模型的完整历史（不含 system）

    返回：
        修复后的新列表；完全干净时返回 None（调用方据此零拷贝快路径）
    """
    if not messages:
        return None

    repaired: list[Any] = []
    changed = False
    for message in messages:
        fixed, msg_changed = sanitize_ai_message(message, recover=False)
        if msg_changed:
            changed = True
        repaired.append(fixed)

    # 孤儿 ToolMessage 清扫（无条件）：id 为空、或配不到任何 AI 消息的调用 → 丢弃。
    # 留着必炸——服务端要求 tool 消息必须紧跟配对的 tool_calls（否则 400）。
    valid_ids = {
        str(call.get("id") or "")
        for message in repaired
        if isinstance(message, AIMessage)
        for call in getattr(message, "tool_calls", None) or []
        if isinstance(call, dict)
    }
    kept: list[Any] = []
    for message in repaired:
        if getattr(message, "type", "") == "tool" and str(
            getattr(message, "tool_call_id", "") or ""
        ) not in valid_ids:
            logger.warning(
                "历史清扫：丢弃孤儿 ToolMessage（tool_call_id=%r 无配对调用）",
                getattr(message, "tool_call_id", None),
            )
            changed = True
            continue
        kept.append(message)
    repaired = kept

    return repaired if changed else None


class ModelOutputSanitizerMiddleware(AgentMiddleware):
    """模型调用边界净化：入向修历史、出向净输出，双向杜绝空 id 入请求体。"""

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：先修历史再调用，最后净化响应（含调用意图恢复）。"""
        # 入向：历史带毒（幻影 call / 孤儿 ToolMessage / 正文壳）先修掉。
        # DEBUG 级：旧带毒线程每轮都会触发，INFO 会刷屏；孤儿清扫另有 WARNING。
        repaired = repair_history(request.messages)
        if repaired is not None:
            logger.debug("模型边界净化：修复历史消息 %d 条", len(repaired))
            request = request.override(messages=repaired)

        result = await handler(request)

        # 出向：新鲜响应净化 + 调用意图恢复（activate_skill 归一化 / 真工具还原）。
        # ⚠️ 形态契约（2026-09-26 泄漏实锤）：正常成功路径 handler 返回的是
        # ModelResponse/ExtendedModelResponse（消息列表壳），AIMessage 只是
        # 兜底中间件的返回形态——三种都要净化，漏了主路径幻影就会直通 state。
        return _sanitize_response(result, _tool_names(request.tools))


def _sanitize_response(result: Any, tool_names: frozenset[str]) -> Any:
    """按 handler 返回形态分发净化；无法识别的形态原样放行（不猜）。"""
    if isinstance(result, ExtendedModelResponse):
        inner = _sanitize_model_response(result.model_response, tool_names)
        return result if inner is result.model_response else replace(
            result, model_response=inner
        )
    if isinstance(result, ModelResponse):
        return _sanitize_model_response(result, tool_names)
    if isinstance(result, AIMessage):
        return sanitize_ai_message(result, tool_names=tool_names, recover=True)[0]
    if isinstance(result, (list, tuple)):
        sanitized = [
            sanitize_ai_message(item, tool_names=tool_names, recover=True)[0]
            for item in result
        ]
        return type(result)(sanitized) if isinstance(result, tuple) else sanitized
    return result


def _sanitize_model_response(
    response: ModelResponse[Any],
    tool_names: frozenset[str],
) -> ModelResponse[Any]:
    """净化 ModelResponse.result 消息列表；无改动时原对象返回（零拷贝快路径）。"""
    sanitized = [
        sanitize_ai_message(item, tool_names=tool_names, recover=True)[0]
        for item in response.result
    ]
    if all(new is old for new, old in zip(sanitized, response.result)) and len(
        sanitized
    ) == len(response.result):
        return response
    return replace(response, result=sanitized)


def _tool_names(tools: list[Any] | None) -> frozenset[str]:
    """从请求工具清单里取名字集合（兼容 BaseTool 与 dict 两种形态）。"""
    names: set[str] = set()
    for tool in tools or []:
        name = getattr(tool, "name", None)
        if name is None and isinstance(tool, dict):
            name = tool.get("name")
        if name:
            names.add(str(name))
    return frozenset(names)
