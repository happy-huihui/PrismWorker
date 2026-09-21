from __future__ import annotations

from typing import Any, Iterable

from harness.prompt import load_text, render_text

"""Lead Agent 系统提示词组装（agents.lead_agent.prompt）

    职责：把各提示词模板 + 运行时信息，装配成模型每轮看到的系统提示。
    模板：全部集中在 harness.prompt（templates/lead_agent/system.md 主模板、
         sandbox_note.md 沙箱段、subagents/routing.md 子代理分节）；本模块只做
         「变量装配」——拼工具清单、按开关取沙箱段、按注册表生成子代理分节。
    边界：技能清单 / 记忆 / 被禁工具等随人随轮变化的内容，仍由各自中间件运行时
         注入（见 skill/memory/deferred 中间件），不在此写死。
"""


def _build_subagent_section(app_config: Any | None, *, has_task: bool) -> str:
    """渲染 <subagent_system> 分节（动态列可用类型 + 真实上限）；不适用时返回空串。

    参数：
        app_config: 应用配置（读 subagents.max_total_per_run 作为每运行硬上限）
        has_task: 本轮工具集是否含 task 工具（不含则不注入该分节）

    返回：
        渲染好的子代理分节字符串，或空串
    """
    # 1.没有 task 工具就没有派发能力，整节省略
    if not has_task:
        return ""

    # 2.从注册表取可用子代理类型（不写死），逐个取描述首行
    from harness.subagents import get_available_subagent_names, get_subagent_config

    names = get_available_subagent_names()
    if not names:
        return ""
    lines: list[str] = []
    for name in names:
        config = get_subagent_config(name)
        desc = (getattr(config, "description", "") or "").strip()
        first_line = desc.splitlines()[0].strip() if desc else "（无描述）"
        lines.append(f"- {name}：{first_line}")

    # 3.每运行子代理总数上限：取真实配置，缺省 10
    cap = 10
    subagents_cfg = getattr(app_config, "subagents", None) if app_config is not None else None
    if subagents_cfg is not None:
        cap = getattr(subagents_cfg, "max_total_per_run", 10) or 10

    # 4.用集中托管的路由模板渲染
    return render_text(
        "subagents/routing",
        {
            "subagent_types": "\n".join(lines),
            "max_total_per_run": str(cap),
        },
    )


def format_system_prompt(
    *,
    agent_name: str,
    tool_names: Iterable[tuple[str, str]],
    sandbox_enabled: bool = False,
    app_config: Any | None = None,
) -> str:
    """把模板填充成最终系统提示词字符串。

    参数：
        agent_name: 角色名（一般取 app_config.agent_name）
        tool_names: 已注册工具的 [(名称, 一句话说明), ...]
        sandbox_enabled: 是否启用沙箱工具（决定是否拼上路径约定段）
        app_config: 应用配置（用于生成子代理分节的每运行上限）；None 用默认

    返回：
        渲染后的系统提示词
    """
    # 1.工具清单规整为列表，并记下是否含 task 工具（决定是否注入子代理分节）
    names: list[str] = []
    has_task = False
    for name, description in tool_names:
        if name == "task":
            has_task = True
        desc = (description or "").strip()
        names.append(f"- {name}：{desc}" if desc else f"- {name}")
    tool_block = "\n".join(names) if names else "（当前没有可用工具，直接基于知识回答）"

    # 2.沙箱段是独立模板，仅在启用时取原文，否则空串
    sandbox_note = load_text("lead_agent/sandbox_note") if sandbox_enabled else ""

    # 3.子代理分节按注册表 + 上限动态渲染（无 task 工具则为空）
    subagent_section = _build_subagent_section(app_config, has_task=has_task)

    # 4.用集中托管的系统模板渲染四个占位符
    return render_text(
        "lead_agent/system",
        {
            "agent_name": agent_name,
            "tool_names": tool_block,
            "subagent_section": subagent_section,
            "sandbox_note": sandbox_note,
        },
    )
