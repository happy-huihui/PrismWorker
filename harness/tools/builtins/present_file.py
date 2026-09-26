
from typing import Annotated
from pathlib import Path

from langchain.tools import InjectedToolCallId, tool
from langgraph.types import Command
from langgraph.config import get_config
from langchain.messages import ToolMessage
from harness.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from harness.tools.types import Runtime
from harness.runtime.user_context import resolve_runtime_user_id



""" 
    Agent 任务完成后，使用此工具将输出文件进行：
        1.工作区校验 -> 必须在当前线程 outputs 目录内
        2.存在性校验 -> 文件必须真实存在，防止"只登记不产出"的幻觉交付
        3.虚拟路径转换 -> 格式统一且不暴露真实磁盘位置
        4.前端点击预览/下载，需要请求虚拟路径，由 resolve_virtual_path() 映射回真实文件，并进行校验再返回
"""

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"

@tool("present_files", parse_docstring=True)
def present_file_tool(
    runtime: Runtime,
    filepaths: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId]
) -> Command :
    """把 Agent 产出的文件登记为可交付产物（present_files）。

    什么时候用：
    - 任务完成，需要把输出文件交给用户预览/下载时。

    什么时候不要用：
    - 中间过程文件（不需要交付给用户）。
    - 图片文件用于查看时（用 view_image）。

    Args:
        filepaths: 要交付的文件路径列表，必须是当前线程 outputs 目录内**确实已存在**的文件
            （沙箱虚拟路径如 /mnt/user-data/outputs/report.md）。

    注意：本工具只负责「登记」，不会替你创建文件。若文件尚未写出，
    会返回错误——请先用写文件的工具把内容落盘，再调用本工具登记。
    """
    try:
        res = list()
        for file_path in filepaths:
            res.append(normialize_result_file(runtime, file_path))
            _ensure_exists(runtime, res[-1], file_path)
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(f"Error: {exc}", tool_call_id=tool_call_id)]},
        )
    return Command(
        update={
            "artifacts": res,
            "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
        },
    )


def _ensure_exists(runtime: Runtime, virtual_path: str, original: str) -> None:
    """校验登记的文件是否真实存在于宿主机磁盘上。

    为什么需要这一步：
        实测（2026-09-23）发现模型会**跳过写文件、直接调 present_files**——
        路径与 outputs 目录的关系校验都能过（因为它确实"看起来"在 outputs 下），
        但磁盘上根本没有这个文件。结果是前端产物栏出现一张点开就 404 的卡片，
        用户拿到的是幻觉交付。

    这里把"存在"作为登记的硬前置：不存在就抛 ValueError，
    工具回一条明确的错误让模型去补写文件，而不是静默登记一个坏产物。
    只检查存在性、不改动文件本身，对 .md/.html/图片 等任何类型都成立。
    """
    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")
    try:
        actual = get_paths().resolve_virtual_path(
            thread_id, virtual_path, user_id=resolve_runtime_user_id(runtime)
        )
    except ValueError:
        # 解析失败时退回用原始入参判断，避免把「路径合法但不存在的文件」误判成越界
        actual = Path(original).expanduser()
    if not actual.is_file():
        raise ValueError(
            f"文件不存在，无法登记为产物：{virtual_path}。"
            f"请先用写文件的工具把内容写入该路径，再调用 present_files。"
        )


def normialize_result_file(
    runtime: Runtime,
    filepath: str
) -> str:
    """
    将 filepath 统一转换成前端/运行时统一识别的虚拟交付路径，并且强制限制在当前线程的 outputs 目录内

    Args:
        - runtime:   当前 Agent 运行上下文，保存线程、用户、状态等信息
        - filepath:
            1.沙箱虚拟路径    `/mnt/user-data/outputs/report.md`
            2.宿主机真实路径  `/app/backend/.deer-flow/threads/<thread>/user-data/outputs/report.md`

    Return:
        `xxx\\.deer-flow\\users\\{user_id}\\threads\\{thread_id}\\user-data\\outputs\\analysis\\report.md`
    """

    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")
    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")

    thread_data = runtime.state.get("thread_data") or  {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    outputs_dir  = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

    if stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/"):
        actual_path = get_paths().resolve_virtual_path(thread_id, filepath, user_id=resolve_runtime_user_id(runtime))
    else:
        actual_path = Path(filepath).expanduser().resolve()

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from exc

    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


"""从运行时上下文或 RunnableConfig 中解析当前线程 ID。"""
def _get_thread_id(runtime: Runtime) -> str | None:

    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return thread_id

    runtime_config = getattr(runtime, "config", None) or {}
    thread_id = runtime_config.get("configurable", {}).get("thread_id")
    if thread_id:
        return thread_id

    try:
        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None























