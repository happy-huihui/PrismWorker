from typing import Any

from langchain.tools import ToolRuntime

from harness.agents.thread_state import ThreadState

Runtime = ToolRuntime[dict[str, Any], ThreadState]

