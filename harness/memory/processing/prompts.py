from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

"""提示词加载与注入格式化（processing.prompts）

    职责：长期记忆的「文本组装」无状态层——模板怎么读、记忆怎么渲染成注入文本。
    内容：
        1. load_prompt / load_prompt_messages   从 YAML 读 text/chat 模板（带缓存）
        2. format_memory_for_injection          记忆文档 → 注入系统提示的纯文本（token 预算裁剪）

    说明：内置模板目录固定在 harness/memory/prompts/（本文件在 processing/ 下，锚点上退一级）；
         外部 prompts_dir 可整体覆盖；对话渲染 format_conversation_for_update 在 messages.py。
"""

# 内置提示词目录（processing/prompts.py → 上退一级到 memory/prompts）
_PROMPTS_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# 模板缓存：(name, prompts_dir) -> text 模板字符串 / chat 原始消息模板
_PROMPT_CACHE: dict[tuple[str, str | None], str] = {}
_CHAT_TEMPLATE_CACHE: dict[tuple[str, str | None], tuple[list[dict[str, str]], str]] = {}


class PromptConfigurationError(ValueError):
    """提示词模板配置错误（bad yaml / 缺 key / 非法占位符）。"""


# ── 模板加载 ─────────────────────────────────────────────────────────────
def load_prompt(name: str, *, prompts_dir: str | None = None) -> str:
    """加载 text 格式提示词模板（.format 语法）。

    参数：
        name: 模板名（对应 {name}.yaml）
        prompts_dir: 外部模板目录；None 用内置 prompts/

    返回：
        模板字符串

    异常：
        模板不存在抛 FileNotFoundError；格式/键不对抛 PromptConfigurationError
    """
    # 1.先查缓存
    cache_key = (name, prompts_dir)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # 2.定位文件（显式目录优先）
    base = Path(prompts_dir) if prompts_dir else _PROMPTS_DEFAULT_DIR
    path = base / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"提示词模板不存在: {name}（查找 {path}）")
    # 3.读 YAML
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PromptConfigurationError(f"提示词 YAML 非法 {path}: {exc}") from exc
    # 4.必须是 text 格式（否则应走 chat 加载器）
    if data.get("format", "text") != "text":
        raise PromptConfigurationError(
            f"{path} 是 chat 格式（format != 'text'），请用 load_prompt_messages"
        )
    # 5.取非空 template 键，缓存后返回
    template = data.get("template")
    if not isinstance(template, str) or not template:
        raise PromptConfigurationError(f"{path} 缺少非空 template 键")
    _PROMPT_CACHE[cache_key] = template
    return template


def load_prompt_messages(
    name: str,
    variables: dict[str, Any],
    *,
    prompts_dir: str | None = None,
) -> list[BaseMessage]:
    """加载并渲染 chat 格式提示词（messages 列表 + .format 变量替换）。

    参数：
        name: 模板名（对应 {name}.chat.yaml）
        variables: 占位符变量
        prompts_dir: 外部模板目录；None 用内置 prompts/

    返回：
        渲染后的 BaseMessage 列表

    异常：
        文件缺失 / 格式不符 / messages 非法 / 占位符非法 → 相应错误
    """
    # 1.命中缓存：复用原始模板，只做变量渲染（缓存的是未渲染模板，不是渲染结果）
    cache_key = (name, prompts_dir)
    cached = _CHAT_TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        raw_templates, source_path = cached
        return _render_messages(raw_templates, variables, source_path)

    # 2.定位 chat 文件
    base = Path(prompts_dir) if prompts_dir else _PROMPTS_DEFAULT_DIR
    path = base / f"{name}.chat.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"chat 提示词模板不存在: {name}（查找 {path}）")
    # 3.读 YAML
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PromptConfigurationError(f"提示词 YAML 非法 {path}: {exc}") from exc
    # 4.必须是 chat 格式
    if data.get("format", "chat") != "chat":
        raise PromptConfigurationError(
            f"{path} 是 text 格式（format != 'chat'），请用 load_prompt"
        )
    # 5.取非空 messages 列表
    msg_list = data.get("messages")
    if not isinstance(msg_list, list) or not msg_list:
        raise PromptConfigurationError(f"{path} 缺少非空 messages 列表")
    # 6.规整成 {role, content} 原始模板（content 一律转字符串）并缓存
    raw_templates: list[dict[str, str]] = []
    for msg in msg_list:
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        raw_templates.append({"role": msg.get("role", "user"), "content": content})
    _CHAT_TEMPLATE_CACHE[cache_key] = (raw_templates, str(path))
    # 7.渲染本次变量
    return _render_messages(raw_templates, variables, str(path))


def _render_messages(
    raw_templates: list[dict[str, str]],
    variables: dict[str, Any],
    source_path: str,
) -> list[BaseMessage]:
    """把模板消息渲染为 BaseMessage（.format 变量替换）。

    参数：
        raw_templates: 未渲染的 {role, content} 列表
        variables: 占位符变量
        source_path: 出错提示用的来源路径

    返回：
        System/Human 消息列表
    """
    messages: list[BaseMessage] = []
    for tmpl in raw_templates:
        content = tmpl["content"]
        # 1.做 .format 变量替换，非法占位符包装成配置错误
        try:
            content = content.format(**variables)
        except (KeyError, ValueError) as exc:
            raise PromptConfigurationError(
                f"{source_path} 存在非法占位符（role={tmpl['role']!r}）: {exc}"
            ) from exc
        # 2.system → SystemMessage，其余 → HumanMessage
        if tmpl["role"] == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


# ── 注入格式化 ───────────────────────────────────────────────────────────
def _est_tokens(text: str) -> int:
    """字符估算 token 数（中英混合折中：约 2 字符/token，无网络依赖）。"""
    if not text:
        return 0
    return math.ceil(len(text) / 2)


def _coerce_confidence(value: Any, default: float = 0.5) -> float:
    """安全取事实置信度（防 None / 字符串 / 越界），钳制到 [0,1]。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    # 非有限数（inf/nan）当默认
    if not math.isfinite(num):
        return default
    return max(0.0, min(num, 1.0))


def _escape_summary(text: str) -> str:
    """注入文本转义（把换行/连续空白压成单空格，防止破坏三段结构）。"""
    return " ".join(text.strip().split())


def format_memory_for_injection(
    memory_data: dict[str, Any],
    max_tokens: int = 2000,
    *,
    guaranteed_categories: list[str] | None = None,
    guaranteed_token_budget: int = 500,
) -> str:
    """把记忆文档格式化为注入文本（三段式 + token 预算裁剪）。

    参数：
        memory_data: memory.json 文档（user/history/facts）
        max_tokens: 主预算（估算 token）
        guaranteed_categories: 必保类别（独立预算优先注入）
        guaranteed_token_budget: 必保类别的独立预算下限

    返回：
        渲染后的注入纯文本（无任何内容时返回空串）

    选择顺序：guaranteed 类别事实（独立预算）→ user 分区 → history 分区
    → 常规事实（置信度降序、共用主预算）。
    """
    # 1.空文档不注入
    if not memory_data:
        return ""
    # 2.必保类别规整成集合（拒裸 str，防逐字符命中）
    if isinstance(guaranteed_categories, str):
        raise TypeError("guaranteed_categories 必须是可迭代的字符串列表，不能是裸 str")
    guarantee_set = (
        frozenset(c.strip() for c in guaranteed_categories if isinstance(c, str) and c.strip())
        if guaranteed_categories
        else frozenset()
    )

    sections: list[str] = []

    # 3.user 上下文分区（Work/Personal/Current Focus 三格）
    user_data = memory_data.get("user", {}) or {}
    user_lines: list[str] = []
    if isinstance(user_data, dict):
        for key, label in (
            ("workContext", "Work"),
            ("personalContext", "Personal"),
            ("topOfMind", "Current Focus"),
        ):
            section = user_data.get(key) or {}
            summary = section.get("summary") if isinstance(section, dict) else None
            if summary:
                user_lines.append(f"{label}: {_escape_summary(str(summary))}")
    if user_lines:
        sections.append("User Context:\n" + "\n".join(f"- {line}" for line in user_lines))

    # 4.history 分区（Recent/Earlier/Background 三格）
    history_data = memory_data.get("history", {}) or {}
    history_lines: list[str] = []
    if isinstance(history_data, dict):
        for key, label in (
            ("recentMonths", "Recent"),
            ("earlierContext", "Earlier"),
            ("longTermBackground", "Background"),
        ):
            section = history_data.get(key) or {}
            summary = section.get("summary") if isinstance(section, dict) else None
            if summary:
                history_lines.append(f"{label}: {_escape_summary(str(summary))}")
    if history_lines:
        sections.append("History:\n" + "\n".join(f"- {line}" for line in history_lines))

    # 5.facts：先筛有效（有 content），再分必保/常规，各自按置信度降序
    facts_data = memory_data.get("facts", []) or []
    valid_facts = [
        f
        for f in facts_data
        if isinstance(f, dict)
        and isinstance(f.get("content"), str)
        and f.get("content", "").strip()
    ]
    facts_block = ""
    if valid_facts:
        guaranteed = [
            f
            for f in valid_facts
            if f.get("category") and f["category"].strip() in guarantee_set
        ]
        regular = [f for f in valid_facts if f not in guaranteed]
        guaranteed.sort(key=_coerce_confidence, reverse=True)
        regular.sort(key=_coerce_confidence, reverse=True)

        def fact_line(fact: dict[str, Any]) -> str:
            # 单条事实渲染成「- [类别] 内容」
            content = _escape_summary(fact["content"])
            category = fact.get("category") or "context"
            return f"- [{category}] {content}"

        # 6.已用预算 = user/history 文本 + "Facts:" 头
        base_text = "\n\n".join(sections)
        used = _est_tokens(base_text) + _est_tokens("Facts:\n")
        # 7.必保类别走独立预算（满则停，不再挤占常规）
        g_lines: list[str] = []
        g_budget = max(50, guaranteed_token_budget)
        for fact in guaranteed:
            line = fact_line(fact)
            if used + _est_tokens(line) > g_budget and g_lines:
                break
            if used + _est_tokens(line) > g_budget and not g_lines:
                break
            g_lines.append(line)
            used += _est_tokens(line)

        # 8.常规事实走主预算（按置信度顺序，装不下即止）
        r_lines: list[str] = []
        total_budget = max(100, max_tokens)
        for fact in regular:
            line = fact_line(fact)
            if used + _est_tokens(line) > total_budget:
                break
            r_lines.append(line)
            used += _est_tokens(line)

        all_lines = g_lines + r_lines
        if all_lines:
            facts_block = "Facts:\n" + "\n".join(all_lines)

    # 9.拼接各段（user/history/facts 间空行分隔）
    if facts_block:
        sections.append(facts_block)
    return "\n\n".join(sections)
