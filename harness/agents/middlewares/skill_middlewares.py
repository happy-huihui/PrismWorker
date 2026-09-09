from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware import types
from langchain_core.messages import SystemMessage

from harness.config.paths import get_paths
from harness.skills.frontmatter import split_skill_markdown

ModelRequest = types.ModelRequest


"""
    技能中间件（skill_middlewares）——技能发现、激活与工具访问策略。

    SkillActivationMiddleware：
      1. abefore_model 扫描技能目录（{base_dir}/skills/*/SKILL.md），用
         frontmatter 解析每个技能的 name/description，把可用技能清单以
         <available_skills> 结构块注入系统消息，让模型知道可以激活哪些技能；
      2. awrap_model_call 检测模型输出中的激活指令标签
         `<activate_skill name="X">`（出现在最新 AI 消息正文里），命中则
         读取该技能 SKILL.md 的 body 作为指令注入系统消息，并把
         SkillEntry 写入 state.skill_context（经由 Command 由 reducer 合并），
         同时追加一条中文进度消息。

    SkillToolPolicyMiddleware：
      最小访问策略——维护 restricted_tools 集合（"需要技能才可调用"的特权
      工具，默认空 = 无特权工具、全部放行）。awrap_model_call 里把已激活技能
      声明可用的工具并与请求工具比对：仅当请求中来自 restricted_tools 的工具
      都被某个已激活技能覆盖时才放行，否则移除并提示。默认空集合 = 直通。
"""

logger = logging.getLogger(__name__)

_DEFAULT_SKILLS_DIR_NAME = "skills"

_ACTIVATE_TAG_RE = re.compile(
    r"<\s*activate_skill\s+name=[\"']([^\"']+)[\"']\s*/?>",
    re.IGNORECASE,
)

_AVAILABLE_MARKER = "available_skills"
_ACTIVE_MARKER = "active_skill"



def scan_skills_dir(skills_dir: Path) -> list[dict[str, str]]:
    """扫描技能目录，返回 [{name, path, description}] 技能清单。

    扫描规则：skills_dir 下每个一级子目录里的 SKILL.md 视为一个技能；
    frontmatter 解析失败或缺 name 的技能会被跳过并记 warning。
    """
    if not skills_dir.is_dir():
        return []
    found: list[dict[str, str]] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            raw = skill_file.read_text(encoding="utf-8")
        except OSError:
            continue
        parts, error = split_skill_markdown(raw)
        if parts is None:
            logger.warning("技能 %s 的 frontmatter 解析失败: %s", child.name, error)
            continue
        metadata = parts.metadata
        name = str(metadata.get("name") or child.name)
        description = str(metadata.get("description") or "")
        found.append({
            "name": name,
            "path": str(skill_file),
            "description": description,
        })
    return sorted(found, key=lambda entry: entry["name"])


def build_available_skills_block(entries: list[dict[str, str]]) -> str:
    """把技能清单拼成 <available_skills> 结构块。"""
    if not entries:
        return f"<{_AVAILABLE_MARKER}>\n（当前没有可用的技能）\n</{_AVAILABLE_MARKER}>"
    lines = []
    for entry in entries:
        description = entry["description"] or "（无描述）"
        lines.append(f'<skill name="{entry["name"]}" path="{entry["path"]}">')
        lines.append(f"  {description}")
        lines.append("</skill>")
    return f"<{_AVAILABLE_MARKER}>\n" + "\n".join(lines) + f"\n</{_AVAILABLE_MARKER}>"


def extract_activation_requests(text: str) -> list[str]:
    """从模型输出文本里提取激活指令的技能名列表（去重保序）。"""
    if not text:
        return []
    names: list[str] = []
    for match in _ACTIVATE_TAG_RE.finditer(text):
        name = match.group(1).strip()
        if name and name not in names:
            names.append(name)
    return names



class SkillActivationMiddleware(AgentMiddleware):
    """技能激活中间件：注入技能清单 + 响应激活指令加载技能正文。"""

    def __init__(self, *, skills_dir: str | Path | None = None) -> None:
        """初始化；skills_dir 指定技能目录（默认 {base_dir}/skills）。"""
        self._skills_dir = skills_dir

    def _resolve_skills_dir(self) -> Path:
        """解析技能目录绝对路径。"""
        if self._skills_dir is not None:
            return Path(self._skills_dir).resolve()
        return (get_paths().base_dir / _DEFAULT_SKILLS_DIR_NAME).resolve()

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：注入技能清单，响应激活指令加载技能正文。"""
        skills_dir = self._resolve_skills_dir()
        entries = scan_skills_dir(skills_dir)
        system_message = request.system_message
        base_text = system_message.text if system_message is not None else ""

        needs_injection = _AVAILABLE_MARKER not in base_text
        if needs_injection and entries:
            block = build_available_skills_block(entries)
            activate_hint = (
                "\n\n如需使用技能，请在回复中包含：<activate_skill name=\"技能名\" />"
            )
            base_text = f"{block}{activate_hint}\n\n{base_text}" if base_text else block + activate_hint

        activation_names = _find_activations_in_messages(request.messages)
        loaded_sections: list[str] = []
        for name in activation_names:
            entry = next((entry for entry in entries if entry["name"] == name), None)
            if entry is None:
                logger.warning("尝试激活不存在的技能: %s", name)
                continue
            body = _load_skill_body(Path(entry["path"]))
            if not body:
                continue
            loaded_sections.append(
                f"<{_ACTIVE_MARKER} name=\"{name}\">\n{body}\n</{_ACTIVE_MARKER}>"
            )
        if loaded_sections:
            base_text = "\n\n".join(loaded_sections) + "\n\n" + base_text

        if needs_injection or loaded_sections:
            new_system = SystemMessage(content=base_text)
            request = request.override(system_message=new_system)
        return await handler(request)

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：把已激活技能写入 state.skill_context 并打印进度。"""
        messages = (state or {}).get("messages") or []
        latest_text = ""
        for message in reversed(messages):
            if message is not None and getattr(message, "type", "") == "ai":
                latest_text = _extract_text(message)
                break
        names = extract_activation_requests(latest_text)
        if not names:
            return None
        updates: dict[str, Any] = {}
        entries = scan_skills_dir(self._resolve_skills_dir())
        by_name = {entry["name"]: entry for entry in entries}
        import time as _time

        skill_entries = []
        for name in names:
            entry = by_name.get(name)
            if entry is None:
                continue
            skill_entries.append({
                "name": entry["name"],
                "path": entry["path"],
                "description": entry["description"],
                "loaded_at": int(_time.time()),
            })
        if skill_entries:
            updates["skill_context"] = skill_entries
            updates["prints"] = [
                f"已激活技能：{', '.join(e['name'] for e in skill_entries)}"
            ]
        return updates or None



class SkillToolPolicyMiddleware(AgentMiddleware):
    """技能工具访问策略（最小实现）：特权工具须有已激活技能覆盖，否则移除。"""

    def __init__(self, *, restricted_tools: list[str] | set[str] | None = None) -> None:
        """初始化；restricted_tools 为需要技能授权的工具名（默认空 = 全部放行）。"""
        self._restricted = frozenset(restricted_tools) if restricted_tools else frozenset()

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        """包装模型调用：按已激活技能过滤特权工具。"""
        if not self._restricted:
            return await handler(request)
        tools = request.tools
        if not tools:
            return await handler(request)

        state = request.state or {}
        skill_context = state.get("skill_context") or []
        activated_paths = {str(entry.get("path", "")) for entry in skill_context}

        kept: list[Any] = []
        blocked: list[str] = []
        for tool in tools:
            name = _tool_name(tool)
            is_restricted = name in self._restricted
            if not is_restricted or activated_paths:
                kept.append(tool)
            else:
                blocked.append(name)

        if not blocked:
            return await handler(request)

        system_message = request.system_message
        text = system_message.text if system_message is not None else ""
        notice = (
            f"提示：以下特权工具当前未授权（需先激活对应技能）："
            f"{', '.join(blocked)}。请先通过 <activate_skill> 激活相关技能后再使用。"
        )
        if system_message is None:
            from langchain_core.messages import SystemMessage

            new_system = SystemMessage(content=notice)
        else:
            text = f"{notice}\n\n{text}" if text else notice
            new_system = system_message.__class__(content=text)
        return await handler(
            request.override(
                tools=kept,
                system_message=new_system,
            )
        )



def _find_activations_in_messages(messages: list[Any] | None) -> list[str]:
    """从消息列表的最新 AI 消息里提取激活技能名列表。"""
    if not messages:
        return []
    text = ""
    for message in reversed(messages):
        if message is None:
            continue
        if getattr(message, "type", "") == "ai":
            text = _extract_text(message)
            break
    return extract_activation_requests(text)


def _load_skill_body(skill_file: Path) -> str:
    """读取 SKILL.md 的 body（frontmatter 之后的内容）。"""
    try:
        raw = skill_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    parts, _ = split_skill_markdown(raw)
    if parts is None:
        return raw
    return parts.body.strip()


def _extract_text(message_or_response: Any) -> str:
    """从消息/响应提取纯文本（兼容 str 与多模态块列表）。"""
    content = getattr(message_or_response, "content", message_or_response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _tool_name(tool: Any) -> str:
    """从（BaseTool | dict schema）里安全提取工具名。"""
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    name = getattr(tool, "name", None)
    return str(name) if name else ""