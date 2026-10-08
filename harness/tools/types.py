from typing import Any

from langchain.tools import ToolRuntime

from harness.agents.thread_state import ThreadState

"""工具运行时类型

    职责：定义工具函数签名里用到的 Runtime 类型别名

    对外暴露：
        - Runtime   ToolRuntime[dict, ThreadState]，工具里取线程 / 用户 / 状态的统一入口
"""

Runtime = ToolRuntime[dict[str, Any], ThreadState]

