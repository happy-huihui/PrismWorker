"""沙箱模块统一出口。

对外暴露：
    - AioSandbox        —— Docker all-in-one-sandbox 客户端封装
    - SandboxManager    —— 容器生命周期管理（docker CLI）
    - make_sandbox_tools—— 把沙箱封装成 LangChain 工具
    - 各种沙箱异常
"""

from __future__ import annotations

from harness.sandbox.aio_sandbox import AioSandbox, GrepMatch, wait_for_sandbox_ready
from harness.sandbox.exceptions import (
    SandboxCommandError,
    SandboxConnectionError,
    SandboxError,
    SandboxFileError,
    SandboxPathError,
)
from harness.sandbox.lifecycle import SandboxManager, get_sandbox_manager
from harness.sandbox.tools import SANDBOX_TOOL_NAMES, make_sandbox_tools

__all__ = [
    "AioSandbox",
    "SandboxManager",
    "get_sandbox_manager",
    "GrepMatch",
    "wait_for_sandbox_ready",
    "make_sandbox_tools",
    "SANDBOX_TOOL_NAMES",
    "SandboxError",
    "SandboxConnectionError",
    "SandboxCommandError",
    "SandboxFileError",
    "SandboxPathError",
]