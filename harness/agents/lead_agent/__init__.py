from harness.agents.lead_agent.agent import assemble_tools, build_lead_agent
from harness.agents.lead_agent.prompt import format_system_prompt

"""Lead Agent 子包

    职责：把模型、工具、中间件、提示词装配成可运行的 Lead Agent 图
        - 提示词：format_system_prompt 做模板填充
        - 组装：build_lead_agent 产出可运行图，assemble_tools 组装工具集

    对外暴露：
        - format_system_prompt()   模板填充（prompt.py）
        - build_lead_agent()       完整组装可运行图（agent.py）
        - assemble_tools()         按配置组装工具集（agent.py）
"""

__all__ = [
    "build_lead_agent",
    "assemble_tools",
    "format_system_prompt",
]
