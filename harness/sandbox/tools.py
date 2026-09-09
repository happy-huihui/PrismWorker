"""沙箱工具生成：把 AioSandbox 封装成 LangChain 工具。

生成六个工具（供 Lead Agent / 子代理注册）：
    - read_file    读取容器内文件
    - write_file   写入容器内文件
    - glob_files   按 glob 模式查找文件
    - grep_files   按正则搜索文件内容
    - list_dir     列出目录内容
    - exec_command 执行 bash 命令

工具风格与本题内置工具保持一致：
    - @tool("name", parse_docstring=True) 装饰
    - runtime: Runtime 注入（用于读取线程/工作区信息）
    - tool_call_id: Annotated[str, InjectedToolCallId] 注入
    - 返回 Command(update={"messages": [ToolMessage(...)]})

设计说明（与用户确认）：
    1. 文件类工具的 path 参数强制校验：必须在 /mnt/user-data 内，
       不允许 .. 穿越（AioSandbox._validate_path 承担）。
    2. exec_command 默认允许任意命令（allow_host_bash=true 语义），
       其 work_dir 参数同样限制在 /mnt/user-data 内。
    3. 命令非零退出码不抛异常，作为文本返回给模型自行修正。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from harness.config.paths import VIRTUAL_PATH_PREFIX
from harness.sandbox.aio_sandbox import AioSandbox
from harness.sandbox.exceptions import SandboxError
from harness.tools.types import Runtime

logger = logging.getLogger(__name__)

SANDBOX_TOOL_NAMES = (
    "read_file",
    "write_file",
    "glob_files",
    "grep_files",
    "list_dir",
    "exec_command",
)


def make_sandbox_tools(
    sandbox: AioSandbox,
    *,
    allow_bash: bool = True,
) -> list[BaseTool]:
    """根据沙箱实例生成全部沙箱工具。

    Args:
        sandbox:    AioSandbox 实例（已连接）
        allow_bash: True 注册 exec_command；False 仅文件工具

    Returns:
        LangChain 工具列表
    """
    tools: list[BaseTool] = [
        tool("read_file", parse_docstring=True)(_make_read_file(sandbox)),
        tool("write_file", parse_docstring=True)(_make_write_file(sandbox)),
        tool("glob_files", parse_docstring=True)(_make_glob_files(sandbox)),
        tool("grep_files", parse_docstring=True)(_make_grep_files(sandbox)),
        tool("list_dir", parse_docstring=True)(_make_list_dir(sandbox)),
    ]
    if allow_bash:
        tools.append(tool("exec_command", parse_docstring=True)(_make_exec_command(sandbox)))
    return tools


def _tool_message(helper: Any, content: str, tool_call_id: str) -> Command:
    """构造工具返回 Command（统一消息格式）。"""
    return Command(update={"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]})


def _extract_user_dir(runtime: Runtime) -> str | None:
    """从线程状态提取用户上传目录（容器内路径），拿不到返回 None。"""
    if runtime.state is None:
        return None
    thread_data = runtime.state.get("thread_data") or {}
    return thread_data.get("uploads_path")



def _make_read_file(sandbox: AioSandbox):
    """读取容器内文件内容（支持行范围）。"""

    def read_file(
        path: str,
        runtime: Runtime,
        tool_call_id: Annotated[str, InjectedToolCallId],
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> Command:
        """读取沙箱工作区内的文件内容。

        Args:
            path: 要读取的文件，必须是 {VIRTUAL_PATH_PREFIX} 下的路径
            start_line: 起始行号（1 起；省略则从文件头）
            end_line: 结束行号（含；省略则到文件尾）
        """
        try:
            content = sandbox.read_file(path, start_line=start_line, end_line=end_line)
        except SandboxError as exc:
            return _tool_message(runtime, f"读取失败: {exc}", tool_call_id)
        return _tool_message(runtime, content, tool_call_id)

    return read_file


def _make_write_file(sandbox: AioSandbox):
    """写入容器内文件（自动创建父目录）。"""

    def write_file(
        path: str,
        content: str,
        runtime: Runtime,
        tool_call_id: Annotated[str, InjectedToolCallId],
        append: bool = False,
    ) -> Command:
        """写入或追加文件到沙箱工作区。

        Args:
            path: 目标文件路径，必须是 {VIRTUAL_PATH_PREFIX} 下的路径
            content: 文件内容
            append: true 时追加到文件末尾，默认覆写整个文件
        """
        try:
            msg = sandbox.write_file(path, content, append=append)
        except SandboxError as exc:
            return _tool_message(runtime, f"写入失败: {exc}", tool_call_id)
        return _tool_message(runtime, msg, tool_call_id)

    return write_file


def _make_glob_files(sandbox: AioSandbox):
    """按 glob 模式查找文件。"""

    def glob_files(
        path: str,
        pattern: str,
        runtime: Runtime,
        tool_call_id: Annotated[str, InjectedToolCallId],
        max_results: int = 200,
    ) -> Command:
        """列出目录下匹配 glob 模式的文件路径。

        Args:
            path: 搜索根目录，必须是 {VIRTUAL_PATH_PREFIX} 下的路径
            pattern: glob 模式，如 "**/*.py" 或 "*.md"
            max_results: 最多返回多少条结果（默认 200）
        """
        try:
            found = sandbox.glob(path, pattern, max_results=max_results)
        except SandboxError as exc:
            return _tool_message(runtime, f"查找失败: {exc}", tool_call_id)
        if not found:
            return _tool_message(runtime, f"在 {path} 下没有匹配 {pattern!r} 的文件", tool_call_id)
        body = f"匹配 {pattern!r} 的文件（共 {len(found)} 个）：\n" + "\n".join(found)
        return _tool_message(runtime, body, tool_call_id)

    return glob_files


def _make_grep_files(sandbox: AioSandbox):
    """按正则搜索文件内容。"""

    def grep_files(
        path: str,
        pattern: str,
        runtime: Runtime,
        tool_call_id: Annotated[str, InjectedToolCallId],
        max_results: int = 100,
    ) -> Command:
        """在目录下的文件中按正则搜索内容。

        Args:
            path: 搜索根目录，必须是 {VIRTUAL_PATH_PREFIX} 下的路径
            pattern: 正则表达式（Python 风格）
            max_results: 最多返回多少条匹配（默认 100）
        """
        try:
            matches, truncated = sandbox.grep(path, pattern, max_results=max_results)
        except SandboxError as exc:
            return _tool_message(runtime, f"搜索失败: {exc}", tool_call_id)
        if not matches:
            return _tool_message(runtime, f"在 {path} 下没有匹配 {pattern!r} 的内容", tool_call_id)
        lines = [f"{m.path}:{m.line_number}: {m.line}" for m in matches]
        body = f"匹配结果（{len(matches)} 条）：\n" + "\n".join(lines)
        if truncated:
            body += "\n...[结果已截断，可缩小范围后重试]"
        return _tool_message(runtime, body, tool_call_id)

    return grep_files


def _make_list_dir(sandbox: AioSandbox):
    """列出目录内容。"""

    def list_dir(
        path: str,
        runtime: Runtime,
        tool_call_id: Annotated[str, InjectedToolCallId],
        max_depth: int = 2,
    ) -> Command:
        """列出沙箱工作区内目录的内容。

        Args:
            path: 要查看的目录，必须是 {VIRTUAL_PATH_PREFIX} 下的路径
            max_depth: 递归深度（默认 2）
        """
        try:
            entries = sandbox.list_dir(path, max_depth=max_depth)
        except SandboxError as exc:
            return _tool_message(runtime, f"列目录失败: {exc}", tool_call_id)
        if not entries:
            return _tool_message(runtime, f"{path} 是空目录", tool_call_id)
        return _tool_message(runtime, "\n".join(entries), tool_call_id)

    return list_dir


def _make_exec_command(sandbox: AioSandbox):
    """在容器内执行 bash（允许任意命令）。"""

    def exec_command(
        command: str,
        runtime: Runtime,
        tool_call_id: Annotated[str, InjectedToolCallId],
        work_dir: str | None = None,
    ) -> Command:
        """在沙箱容器内执行 bash 命令。

        可执行任意命令（安装工具 / 运行脚本 / 编译程序等）。
        命令在容器内运行，与宿主机隔离。

        Args:
            command: 要执行的 bash 命令
            work_dir: 命令工作目录，必须是 {VIRTUAL_PATH_PREFIX} 下的路径
                      （省略则使用沙箱默认工作目录 /mnt/user-data）
        """
        try:
            output = sandbox.exec_command(command, exec_dir=work_dir)
        except SandboxError as exc:
            return _tool_message(runtime, f"命令执行失败: {exc}", tool_call_id)
        return _tool_message(runtime, output, tool_call_id)

    return exec_command


def sandbox_default_workdir() -> str:
    """返回沙箱工具默认工作目录（模型提示词中用）。"""
    return VIRTUAL_PATH_PREFIX