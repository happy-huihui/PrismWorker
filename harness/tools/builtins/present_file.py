
from typing import Annotated, Any
from pathlib import Path

from langchain.tools import InjectedToolCallId, tool
from langgraph.types import Command
from langgraph.config import get_config
from langchain.messages import ToolMessage
from harness.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from harness.tools.types import Runtime
from harness.runtime.user_context import resolve_runtime_user_id
from harness.sandbox.exceptions import SandboxError



"""产物登记工具

    职责：把 Agent 产出的文件登记为可交付产物（present_files）
        - 工作区校验：必须落在当前线程 outputs 目录内
        - 存在性校验：文件必须真实存在（防「只登记不产出」的幻觉交付）
        - 虚拟路径转换：统一成前端可解析的虚拟路径，不暴露真实磁盘位置

    对外暴露：
        - present_file_tool        工具本体（写 state.artifacts）
        - normalize_result_file   路径归一为 outputs 虚拟路径（越界即报错）
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
        # 1.逐条归一为 outputs 虚拟路径（越界 / 不合法会抛 ValueError）
        res = list()
        for file_path in filepaths:
            res.append(normalize_result_file(runtime, file_path))
            # 2.登记前做存在性硬校验，防止登记「点开就 404」的幻觉产物
            _ensure_exists(runtime, res[-1], file_path)
    # 3.校验失败回一条明确错误，让模型先去把文件写出来
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(f"Error: {exc}", tool_call_id=tool_call_id)]},
        )
    # 4.成功：把虚拟路径写进 state.artifacts（前端产物栏按此渲染）
    return Command(
        update={
            "artifacts": res,
            "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
        },
    )


def _ensure_exists(runtime: Runtime, virtual_path: str, original: str) -> None:
    """校验登记的文件确实存在（挂载感知：已挂载查宿主，未挂载查容器）。

    为什么需要这一步：
        实测（2026-09-23）发现模型会跳过写文件、直接调 present_files，导致前端
        出现「点开就 404」的幻觉交付。所以把「存在」作为登记的硬前置。

    为什么挂载感知（2026-10-06 复发修复）：
        沙箱容器里的 /mnt/user-data 只有在 bind 挂载到宿主时才与宿主是同一份文件；
        未挂载时它是容器本地目录，宿主要等 run 收尾回传才看得到，此时只查宿主
        必然误报「文件不存在」。故分两条路：
        - 已挂载 → 查宿主（快，且能挡住幻觉交付）
        - 未挂载 → 查容器（sandbox.read_file 探测），文件在容器即可登记，由收尾回传同步回宿主
    """
    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")

    # 1.已挂载：宿主与容器同一份文件，直接查宿主
    if _is_mounted(thread_id):
        _ensure_host_exists(runtime, thread_id, virtual_path, original)
        return

    # 2.未挂载：宿主看不到容器内文件，改查容器；拿不到沙箱则退回宿主校验
    sandbox = _get_sandbox()
    if sandbox is None:
        _ensure_host_exists(runtime, thread_id, virtual_path, original)
        return
    # 读文件探测容器内是否存在：读成功=存在；抛 SandboxFileError=不存在/读不到
    try:
        sandbox.read_file(virtual_path)
    except SandboxError:
        raise ValueError(
            f"文件不存在，无法登记为产物：{virtual_path}。"
            f"请先用写文件的工具把内容写入该路径，再调用 present_files。"
        )


def _ensure_host_exists(runtime: Runtime, thread_id: str, virtual_path: str, original: str) -> None:
    """查宿主机磁盘是否存在该文件（已挂载时的标准路径）。"""
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


def _is_mounted(thread_id: str) -> bool:
    """判断本线程沙箱是否 bind 挂载了宿主工作区。"""
    try:
        from harness.runtime.sandbox import is_workspace_mounted

        return bool(is_workspace_mounted(thread_id))
    except Exception:  # noqa: BLE001 —— 判断失败按未挂载处理，走容器探测
        return False


def _get_sandbox() -> Any | None:
    """取当前线程所属沙箱实例（未挂载时用它查容器内文件）；取不到返回 None。"""
    try:
        from harness.runtime.sandbox import get_app_sandbox

        return get_app_sandbox()
    except Exception:  # noqa: BLE001 —— 取不到降级返回 None，走宿主校验
        return None


def normalize_result_file(
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

    # 1.线程数据是定位 outputs 目录的前提，缺任一都报错
    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")
    thread_id = _get_thread_id(runtime)
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context or runtime config")

    thread_data = runtime.state.get("thread_data") or  {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    # 2.先把 outputs 目录解析成绝对路径，作为后续越界判断的基准
    outputs_dir  = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

    # 3.沙箱虚拟路径走 resolve_virtual_path 映射；宿主真实路径直接 resolve
    if stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/"):
        actual_path = get_paths().resolve_virtual_path(thread_id, filepath, user_id=resolve_runtime_user_id(runtime))
    else:
        actual_path = Path(filepath).expanduser().resolve()

    try:
        # 4.relative_to 失败即越界：只有 outputs 目录下的文件能登记
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from exc

    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


"""从运行时上下文或 RunnableConfig 中解析当前线程 ID。"""
def _get_thread_id(runtime: Runtime) -> str | None:

    # 1.优先 runtime.context
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return thread_id

    # 2.退到 runtime.config.configurable
    runtime_config = getattr(runtime, "config", None) or {}
    thread_id = runtime_config.get("configurable", {}).get("thread_id")
    if thread_id:
        return thread_id

    # 3.再退到图全局 config（无运行时上下文时抛 RuntimeError）
    try:
        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None























