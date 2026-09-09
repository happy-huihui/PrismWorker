from __future__ import annotations

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
      返回一条提示"先 read 再 write"的 ToolMessage。新建文件例外可用
      参数 allow_unread_creates 放行（默认拦截，保守优先）。

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



class ReadBeforeWriteMiddleware(AgentMiddleware):
    """写前读中间件：写文件前必须已有对应读取记录，否则拦截。"""

    def __init__(
        self,
        *,
        write_tools: list[str] | set[str] | None = None,
        allow_unread_creates: bool = False,
    ) -> None:
        """初始化；write_tools 覆盖写工具集合，allow_unread_creates 放行新文件。"""
        self._write_tools = frozenset(write_tools) if write_tools else _DEFAULT_WRITE_TOOLS
        self._allow_unread_creates = allow_unread_creates

    async def awrap_tool_call(
        self,
        request: ToolCallRequest[Any],
        handler: Callable[[ToolCallRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装写工具调用：未先读则拦截并提示。"""
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
        if self._allow_unread_creates and (args.get("create") is True or args.get("create_new") is True):
            return await handler(request)
        message = (
            f"写文件被拦截：目标 {target} 尚未被读取。为避免覆盖未知内容，"
            "请先用 read_file 读取该路径（若确实要创建全新文件，请带上 "
            "create=true 参数重试）。"
        )
        result = ToolMessage(
            content=message,
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=name,
        )
        result.id = str(request.tool_call.get("id") or "")
        return result



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