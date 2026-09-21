from __future__ import annotations

import math
from typing import Any

"""记忆注入渲染（processing.injection）

    职责：把 memory.json 文档渲染成可注入系统提示的纯文本（User Context /
         History / Facts 三段），按 token 预算裁剪；guaranteed 类别事实优先占位。
    定位：这是「输出渲染」，不是「模型指令模板」，所以留在 memory 子系统，
         不放 harness/prompt。（提示词模板本身已上收到 harness.prompt。）
    无依赖：字符估算 token（CJK 友好），不联网、无外部调用。
"""


def _est_tokens(text: str) -> int:
    """字符估算 token 数（中英混合折中：约 2 字符/token）。"""
    # 空串 0；否则按长度一半向上取整
    if not text:
        return 0
    return math.ceil(len(text) / 2)


def _coerce_confidence(value: Any, default: float = 0.5) -> float:
    """安全取事实置信度（防 None / bool / 字符串 / 越界 / 非有限数）。

    参数：
        value: 原始置信度
        default: 兜底值

    返回：
        钳制到 [0,1] 的浮点置信度
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    # inf/nan 当默认
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

    # 5.facts：筛有效 → 分必保/常规 → 各自置信度降序 → 按预算拼装
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
        # 5.1 命中必保类别的走独立预算，其余为常规
        guaranteed = [
            f
            for f in valid_facts
            if f.get("category") and f["category"].strip() in guarantee_set
        ]
        regular = [f for f in valid_facts if f not in guaranteed]
        # 5.2 两组各自按置信度降序
        guaranteed.sort(key=_coerce_confidence, reverse=True)
        regular.sort(key=_coerce_confidence, reverse=True)

        def fact_line(fact: dict[str, Any]) -> str:
            # 单条事实渲染成「- [类别] 内容」
            content = _escape_summary(fact["content"])
            category = fact.get("category") or "context"
            return f"- [{category}] {content}"

        # 5.3 已用预算 = user/history 文本 + "Facts:" 头
        base_text = "\n\n".join(sections)
        used = _est_tokens(base_text) + _est_tokens("Facts:\n")
        # 5.4 必保类别：独立预算内优先塞入（满则停）
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

        # 5.5 常规事实：主预算内按序追加，装不下即止
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

    # 6.拼接各段（段间空行）
    if facts_block:
        sections.append(facts_block)
    return "\n\n".join(sections)
