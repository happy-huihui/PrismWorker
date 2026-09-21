"""Lead Agent 子包：系统提示词模板与组装逻辑。

对外暴露：
    - format_system_prompt()   模板填充（prompt.py）
    - build_lead_agent()       完整组装可运行图（agent.py）
    - assemble_tools()         按配置组装工具集（agent.py）
"""

from harness.agents.lead_agent.agent import assemble_tools, build_lead_agent
from harness.agents.lead_agent.prompt import format_system_prompt

__all__ = [
    "build_lead_agent",
    "assemble_tools",
    "format_system_prompt",
]