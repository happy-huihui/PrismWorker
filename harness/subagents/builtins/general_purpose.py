"""内置 general_purpose 子代理的配置定义。"""

from harness.prompt import load_text
from harness.subagents.config import SubagentConfig

GENERAL_PURPOSE_CONFIG = SubagentConfig(
    name="general_purpose",
    description="""适合承担被独立派发的任务：能分工并行、需要上下文隔离、
需要专门工具或模型的活儿可以交给它；琐碎、强依赖、要用户交互的不要派。""",
    # 子代理 system 提示词模板集中在 harness/prompt（无变量，取原文）
    system_prompt=load_text("subagents/general_purpose"),
    tools=None,
    disallowed_tools=["task"],
    model="inherit",
    max_turns=50,
    timeout_seconds=600,
)