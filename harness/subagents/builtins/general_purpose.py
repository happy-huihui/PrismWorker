from harness.prompt import load_text
from harness.subagents.config import SubagentConfig

"""general_purpose 子代理配置

    职责：定义唯一内置子代理 general_purpose 的配置
        - 提示词取自 harness/prompt 的 subagents/general_purpose
        - 继承父级模型，禁用 task（防递归派发）

    对外暴露：
        - GENERAL_PURPOSE_CONFIG
"""

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
