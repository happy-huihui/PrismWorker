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
from harness.sandbox.warm_pool import WarmPool
from harness.sandbox.tools import SANDBOX_TOOL_NAMES, make_sandbox_tools

"""沙箱模块出口

    职责：聚合沙箱层的客户端、生命周期、热池、工具与异常，供上层一处导入
        - 容器客户端与就绪探测
        - 容器生命周期（含热池）
        - 热池数据结构与策略
        - 工具工厂与异常体系

    对外暴露：
        - AioSandbox / GrepMatch / wait_for_sandbox_ready                  容器客户端与就绪探测
        - SandboxManager / get_sandbox_manager                             容器生命周期管理（含热池）
        - WarmPool                                                         热池纯数据结构
        - make_sandbox_tools / SANDBOX_TOOL_NAMES                          沙箱工具工厂
        - SandboxError / SandboxConnectionError / SandboxCommandError      异常基类 / 连接 / 命令
        - SandboxFileError / SandboxPathError                              文件操作 / 路径越界
"""

__all__ = [
    "AioSandbox",
    "SandboxManager",
    "get_sandbox_manager",
    "WarmPool",
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
