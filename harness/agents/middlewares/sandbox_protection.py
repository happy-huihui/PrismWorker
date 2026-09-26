from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import ToolMessage

ToolCallRequest = types.ToolCallRequest

"""
    沙箱保护中间件（sandbox_protection）——写前读与命令审计两道闸。

    ReadBeforeWriteMiddleware（写前读）：
      防止 Agent 在未读取目标文件的情况下盲目覆盖写文件（可能覆盖掉
      用户数据或不知情的内容）。对写类工具（write_file / append_file /
      overwrite_file / edit_file…）做检查：目标 path 必须出现在历史
      读取记录（read_file / view_file 的调用参数）里，否则拦截该调用并
      返回一条提示"先 read 再 write"的 ToolMessage。

      两道放行口（2026-09-25 修订）：
        1. 目标**已存在性探测**（exists_probe）判为「不存在」→ 直接放行。
           新建文件没有可覆盖的内容，拦下来只会让模型白折腾：实测模型被拦后
           反复重试，整轮被拖死。
        2. allow_unread_creates=True 时，允许模型显式声明 create=true 绕过
           （默认关闭；注意这条**必须**与工具 schema 里的 create 参数同时存在，
           否则提示会让模型去传一个根本不存在的参数——旧版就是这么写的）。

    SandboxAuditMiddleware（命令审计）：
      对沙箱命令执行工具（exec_command / bash / shell…）的每次调用做
      审计记录：命令摘要、是否成功、时间戳。记录写入 self.audit_log
      （进程内列表，运行层可读取），同时向 prints 写一行中文进度。
"""

logger = logging.getLogger(__name__)

_DEFAULT_WRITE_TOOLS = frozenset({
    "write_file",
    "append_file",
    "overwrite_file",
    "edit_file",
    "overwrite",
    "write",
})
_READ_TOOLS = frozenset({"read_file", "view_file", "cat"})
_DEFAULT_COMMAND_TOOLS = frozenset({"exec_command", "bash", "shell", "run_command"})

# 存在性探测的超时：探测卡住时不能让写工具跟着卡住
_PROBE_TIMEOUT_SECONDS = 10.0



class ReadBeforeWriteMiddleware(AgentMiddleware):
    """写前读中间件：写文件前必须已有对应读取记录，否则拦截。"""

    def __init__(
        self,
        *,
        write_tools: list[str] | set[str] | None = None,
        allow_unread_creates: bool = False,
        exists_probe: Callable[[str], bool | None] | None = None,
    ) -> None:
        """初始化。

        参数：
            write_tools: 覆盖写工具集合
            allow_unread_creates: 是否允许模型用 create=true 显式声明新建
                （默认关闭；开启时必须同时给工具 schema 加上 create 参数，
                否则等于让模型去传一个不存在的参数）
            exists_probe: 目标存在性探测（同步可调用，如 sandbox.file_exists）。
                返回 False 表示确认不存在 → 直接放行；返回 True/None → 按原逻辑
                拦截。None 表示不探测（退回纯「写前读」语义）。
        """
        self._write_tools = frozenset(write_tools) if write_tools else _DEFAULT_WRITE_TOOLS
        self._allow_unread_creates = allow_unread_creates
        self._exists_probe = exists_probe

    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装写工具调用：未先读则拦截并提示（新建文件除外）。"""
        name = str(request.tool_call.get("name") or "")
        if name not in self._write_tools:
            return await handler(request)
        args = request.tool_call.get("args") or {}
        target = _extract_path(args)
        if not target:
            return await handler(request)
        read_paths = _collect_read_paths(request.state)
        if _path_matches(target, read_paths):
            return await handler(request)

        # 目标不存在 = 新建：没有可覆盖的内容，直接放行（避免把模型拦进重试死循环）
        if self._exists_probe is not None and await self._probe_exists(target) is False:
            return await handler(request)

        if self._allow_unread_creates and (args.get("create") is True or args.get("create_new") is True):
            return await handler(request)
        message = (
            f"写文件被拦截：目标 {target} 已存在且尚未被读取。为避免覆盖未知内容，"
            "请先用 read_file 读取该路径，确认内容后再写。"
        )
        result = ToolMessage(
            content=message,
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=name,
        )
        result.id = str(request.tool_call.get("id") or "")
        return result

    async def _probe_exists(self, target: str) -> bool | None:
        """探测目标是否已存在。

        为什么放线程池：探测是同步 HTTP 调用，直接在协程里跑会阻塞事件循环
        （本项目已因同类问题踩过坑）。为什么加超时：探测卡住不该把写工具一起拖住。

        参数：
            target: 容器内路径

        返回：
            True / False / None（None = 探测失败，调用方按「已存在」保守处理）
        """
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._exists_probe, target),
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 —— 探测失败必须保守：宁可拦一下，也不放行覆盖
            logger.debug("写前读存在性探测失败，按「已存在」处理: %s", target, exc_info=True)
            return None



class SandboxAuditMiddleware(AgentMiddleware):
    """命令审计中间件：记录沙箱命令执行日志并写进度。"""

    def __init__(
        self,
        *,
        command_tools: list[str] | set[str] | None = None,
        max_audit_entries: int = 200,
    ) -> None:
        """初始化；command_tools 覆盖命令工具集合，max_audit_entries 日志上限。"""
        self._command_tools = frozenset(command_tools) if command_tools else _DEFAULT_COMMAND_TOOLS
        self.audit_log: list[dict[str, Any]] = []
        self._max_entries = max(10, max_audit_entries)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装命令工具调用：执行前后记录审计日志。"""
        name = str(request.tool_call.get("name") or "")
        if name not in self._command_tools:
            return await handler(request)
        command_preview = _command_preview(request.tool_call.get("args") or {})
        call_id = str(request.tool_call.get("id") or "")

        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001 —— 审计记录后再抛给上层
            self._record_audit(name, command_preview, success=False, error=str(exc))
            raise

        self._record_audit(name, command_preview, success=True, call_id=call_id)
        return result

    def _record_audit(self, name: str, command: str, *, success: bool, error: str = "", call_id: str = "") -> None:
        """写入一条审计日志并裁剪超限条目。"""
        import time

        entry = {
            "tool": name,
            "command_preview": command,
            "success": success,
            "error": error[:200] if error else None,
            "tool_call_id": call_id,
            "ts": time.time(),
        }
        self.audit_log.append(entry)
        if len(self.audit_log) > self._max_entries:
            self.audit_log = self.audit_log[-self._max_entries:]
        preview = command if len(command) <= 80 else command[:80] + "…"
        status = "成功" if success else "失败"
        logger.info("命令审计 [%s] %s: %s", status, name, preview)



def _extract_path(args: dict[str, Any]) -> str:
    """从工具参数里提取目标路径（常见字段名枚举）。"""
    for key in ("path", "file_path", "filepath", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = args.get("name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _collect_read_paths(state: Any) -> set[str]:
    """从消息历史收集所有读取过的路径（read/view 类工具的调用参数）。"""
    if state is None:
        return set()
    messages = (state or {}).get("messages") or []
    read_paths: set[str] = set()
    for message in messages:
        if message is None or getattr(message, "type", "") != "ai":
            continue
        for call in getattr(message, "tool_calls", None) or []:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "")
            if name not in _READ_TOOLS:
                continue
            path = _extract_path(call.get("args") or {})
            if path:
                read_paths.add(path)
    return read_paths


def _path_matches(target: str, read_paths: set[str]) -> bool:
    """判断目标路径是否命中已读集合（支持精确或结尾目录匹配）。"""
    if target in read_paths:
        return True
    return any(target.startswith(prefix.rstrip("/") + "/") for prefix in read_paths if prefix)


def _command_preview(args: dict[str, Any]) -> str:
    """把命令工具参数压缩成一行预览（优先 command 字段）。"""
    for key in ("command", "cmd", "script"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return _collapse_whitespace(value)
    try:
        import json

        return json.dumps(args, ensure_ascii=False)[:200]
    except Exception:  # noqa: BLE001
        return str(args)[:200]


def _collapse_whitespace(text: str) -> str:
    """把命令文本压成单行（换行/连续空白 → 空格）。"""
    return " ".join(text.split())[:200]