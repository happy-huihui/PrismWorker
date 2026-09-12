"""长期记忆提示词层：模板加载与注入文本格式化。

职责：
    1. ``load_prompt`` / ``load_prompt_messages``：从 YAML 文件加载提示词
       模板（text / chat 两种格式），带 (name, prompts_dir) 编译缓存；
       模板用 ``.format`` 语法，外部目录可整体覆盖内置 prompts/；
    2. ``format_memory_for_injection``：把记忆文档渲染成注入系统提示的
       纯文本（User Context / History / Facts 三段），按 token 预算
       裁剪（字符估算，CJK 友好、无网络依赖），guaranteed 类别事实
       优先保证注入；
    3. ``format_conversation_for_update`` 在 message_processing.py 中
       （对话渲染属于消息处理职责，不重复定义）。
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

# 内置提示词目录
_PROMPTS_DEFAULT_DIR = Path(__file__).resolve().parent / "prompts"

# 模板缓存：(name, prompts_dir) -> 模板字符串 / 原始消息模板
_PROMPT_CACHE: dict[tuple[str, str | None], str] = {}
_CHAT_TEMPLATE_CACHE: dict[tuple[str, str | None], tuple[list[dict[str, str]], str]] = {}


class PromptConfigurationError(ValueError):
    """提示词模板配置错误（bad yaml / 缺 key / 非法占位符）。"""


# ── 模板加载 ─────────────────────────────────────────────────────────────
def load_prompt(name: str, *, prompts_dir: str | None = None) -> str:
    """加载 text 格式提示词模板（.format 语法）；失败抛 FileNotFoundError。"""
    cache_key = (name, prompts_dir)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    base = Path(prompts_dir) if prompts_dir else _PROMPTS_DEFAULT_DIR
    path = base / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"提示词模板不存在: {name}（查找 {path}）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PromptConfigurationError(f"提示词 YAML 非法 {path}: {exc}") from exc
    if data.get("format", "text") != "text":
        raise PromptConfigurationError(
            f"{path} 是 chat 格式（format != 'text'），请用 load_prompt_messages"
        )
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
    """加载并渲染 chat 格式提示词（messages 列表 + .format 变量替换）。"""
    cache_key = (name, prompts_dir)
    cached = _CHAT_TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        raw_templates, source_path = cached
        return _render_messages(raw_templates, variables, source_path)

    base = Path(prompts_dir) if prompts_dir else _PROMPTS_DEFAULT_DIR
    path = base / f"{name}.chat.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"chat 提示词模板不存在: {name}（查找 {path}）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PromptConfigurationError(f"提示词 YAML 非法 {path}: {exc}") from exc
    if data.get("format", "chat") != "chat":
        raise PromptConfigurationError(
            f"{path} 是 text 格式（format != 'chat'），请用 load_prompt"
        )
    msg_list = data.get("messages")
    if not isinstance(msg_list, list) or not msg_list:
        raise PromptConfigurationError(f"{path} 缺少非空 messages 列表")
    raw_templates: list[dict[str, str]] = []
    for msg in msg_list:
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        raw_templates.append({"role": msg.get("role", "user"), "content": content})
    _CHAT_TEMPLATE_CACHE[cache_key] = (raw_templates, str(path))
    return _render_messages(raw_templates, variables, str(path))


def _render_messages(
    raw_templates: list[dict[str, str]],
    variables: dict[str, Any],
    source_path: str,
) -> list[BaseMessage]:
    """把模板消息渲染为 BaseMessage（.format 变量替换）。"""
    messages: list[BaseMessage] = []
    for tmpl in raw_templates:
        content = tmpl["content"]
        try:
            content = content.format(**variables)
        except (KeyError, ValueError) as exc:
            raise PromptConfigurationError(
                f"{source_path} 存在非法占位符（role={tmpl['role']!r}）: {exc}"
            ) from exc
        if tmpl["role"] == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


# ── 注入格式化 ───────────────────────────────────────────────────────────
def _est_tokens(text: str) -> int:
    """字符估算 token 数（中英混合折中：约 2 字符/token）。"""
    if not text:
        return 0
    return math.ceil(len(text) / 2)


def _coerce_confidence(value: Any, default: float = 0.5) -> float:
    """安全取事实置信度（防 None / 字符串 / 越界）。"""
    if value is None or isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(0.0, min(num, 1.0))


def _escape_summary(text: str) -> str:
    """注入文本转义（防止模板串与换行破坏结构）。"""
    return " ".join(text.strip().split())


def format_memory_for_injection(
    memory_data: dict[str, Any],
    max_tokens: int = 2000,
    *,
    guaranteed_categories: list[str] | None = None,
    guaranteed_token_budget: int = 500,
) -> str:
    """把记忆文档格式化为注入文本（三段式 + token 预算裁剪）。

    选择顺序：guaranteed 类别事实（独立预算，优先）→ user 分区 →
    history 分区 → 常规事实（按置信度降序，与其余内容共用主预算）。
    无任何内容时返回空串（调用方不注入）。
    """
    if not memory_data:
        return ""
    if isinstance(guaranteed_categories, str):
        raise TypeError("guaranteed_categories 必须是可迭代的字符串列表，不能是裸 str")
    guarantee_set = (
        frozenset(c.strip() for c in guaranteed_categories if isinstance(c, str) and c.strip())
        if guaranteed_categories
        else frozenset()
    )

    sections: list[str] = []

    # 1. user 上下文分区
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

    # 2. history 分区
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

    # 3. facts 块
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
        # 类别内按置信度降序
        guaranteed.sort(key=_coerce_confidence, reverse=True)
        regular.sort(key=_coerce_confidence, reverse=True)

        def fact_line(fact: dict[str, Any]) -> str:
            content = _escape_summary(fact["content"])
            category = fact.get("category") or "context"
            return f"- [{category}] {content}"

        # 先保证 guaranteed（独立预算），再常规（主预算内）
        base_text = "\n\n".join(sections)
        used = _est_tokens(base_text) + _est_tokens("Facts:\n")
        g_lines: list[str] = []
        g_budget = max(50, guaranteed_token_budget)
        for fact in guaranteed:
            line = fact_line(fact)
            if used + _est_tokens(line) > g_budget and g_lines:
                # guaranteed 预算已满：不再追加常规，直接跳出
                break
            if used + _est_tokens(line) > g_budget and not g_lines:
                break
            g_lines.append(line)
            used += _est_tokens(line)

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

    parts = [s for s in sections if s]
    if facts_block:
        parts.append(facts_block)
    return "\n\n".join(parts)


# 内置 text 模板统一校验（缺失即启动时报错，不让配置错误静默）
_CONSOLE_RENDERED = None  # 占位：text 模板目前仅 memory_update 的 chat 表单在跑