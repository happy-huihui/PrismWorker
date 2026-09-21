from __future__ import annotations

import logging
from pathlib import Path
from string import Template
from typing import Any, Mapping

import yaml
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

"""提示词加载与渲染（prompt.loader）

    职责：全项目「模型指令模板」的统一读取与渲染入口——定位模板文件、
         按 $var 填充、chat 模板渲染成消息序列。只认文本与结构化，不含任何
         子系统的业务变量装配（那是各调用方的事）。
    存放：模板默认在本包 templates/ 下，一文件一模板，按域分子目录：
        - 纯文本模板：<name>.md      （如 lead_agent/system、summarizer/summary）
        - chat 多角色：<name>.chat.yaml（如 memory/memory_update）
    语法：string.Template 的 $var —— JSON 里的 {} 无需转义（相比 .format 的净收益）。
    覆盖：base_dir 可指向外部目录整体替换内置模板（memory 的 prompts_dir 就靠它）。
    缓存：原文按 (name, base_dir) 缓存；渲染每次执行（变量不同）。

    输出数据示例：
        render_text("summarizer/summary", {"messages": "A/B"})
          -> "你是对话摘要助手。请把下面的对话历史压缩成...：\n<历史消息>\nA/B\n</历史消息>\n直接输出摘要正文..."
        load_chat("memory/memory_update", {"current_memory": "{}", "conversation": "C", "correction_hint": ""})
          -> [SystemMessage(...), HumanMessage(...)]
"""

# 内置模板根目录
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# 纯文本模板缓存：(name, base_dir) -> 原文
_TEXT_CACHE: dict[tuple[str, str | None], str] = {}
# chat 模板缓存：(name, base_dir) -> (原始 messages 列表, 来源路径)
_CHAT_CACHE: dict[tuple[str, str | None], tuple[list[dict[str, str]], str]] = {}


class PromptError(RuntimeError):
    """提示词层错误基类。"""


class PromptNotFound(PromptError, FileNotFoundError):
    """模板文件不存在（显式目录缺失要显眼报错）。"""


class PromptConfigurationError(PromptError):
    """模板结构/占位符非法（缺 key、messages 非列表、变量缺失等）。"""


def _resolve_base(base_dir: str | Path | None) -> Path:
    """解析模板根目录：显式 base_dir 优先，否则内置 templates。"""
    # 1.调用方覆盖目录（如 memory 的 prompts_dir）
    if base_dir:
        return Path(base_dir)
    # 2.缺省用本包内置模板根
    return TEMPLATES_DIR


def load_text(name: str, *, base_dir: str | Path | None = None) -> str:
    """读取纯文本模板原文（不做变量替换）。

    参数：
        name: 模板名（对应 templates/{name}.md，可含子目录如 lead_agent/system）
        base_dir: 覆盖模板根目录；None 用内置 templates

    返回：
        模板原文（缓存）

    异常：
        文件不存在抛 PromptNotFound

    用途：无变量模板（子代理 system、沙箱段）直接取原文。
    """
    cache_key = (name, str(base_dir) if base_dir else None)
    cached = _TEXT_CACHE.get(cache_key)
    # 1.命中缓存
    if cached is not None:
        return cached
    # 2.定位并读取 .md
    path = _resolve_base(base_dir) / f"{name}.md"
    if not path.is_file():
        raise PromptNotFound(f"文本模板不存在: {name}（查找 {path}）")
    raw = path.read_text(encoding="utf-8")
    # 3.缓存原文
    _TEXT_CACHE[cache_key] = raw
    return raw


def render_text(
    name: str,
    variables: Mapping[str, Any] | None = None,
    *,
    base_dir: str | Path | None = None,
) -> str:
    """按 $var 渲染纯文本模板。

    参数：
        name: 模板名（对应 templates/{name}.md）
        variables: 占位符变量映射；模板无 $var 时可省略
        base_dir: 覆盖模板根目录

    返回：
        渲染后的字符串

    异常：
        模板缺失 / 占位符非法 / 变量缺失 → 相应错误
    """
    # 1.取原文
    template = Template(load_text(name, base_dir=base_dir))
    # 2.无变量：直接返回原文（Template 对不含 $ 的文本原样输出）
    if not variables:
        return template.template
    # 3.严格替换：缺变量或非法占位符都抛错（配置错误要显眼）
    try:
        return template.substitute(dict(variables))
    except KeyError as exc:
        raise PromptConfigurationError(f"模板 {name} 缺少占位符变量: {exc}") from exc
    except ValueError as exc:
        raise PromptConfigurationError(f"模板 {name} 存在非法占位符: {exc}") from exc


def load_chat(
    name: str,
    variables: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> list[BaseMessage]:
    """加载并渲染 chat 多角色模板（{name}.chat.yaml）。

    参数：
        name: 模板名（对应 templates/{name}.chat.yaml，含 format/messages）
        variables: 每条消息正文 .format 位点的 $var 映射
        base_dir: 覆盖模板根目录

    返回：
        BaseMessage 列表（system→SystemMessage，其余→HumanMessage）

    异常：
        文件缺失 / YAML 非法 / messages 结构错 / 占位符非法 → 相应错误
    """
    cache_key = (name, str(base_dir) if base_dir else None)
    cached = _CHAT_CACHE.get(cache_key)
    # 1.命中缓存：复用未渲染模板，只做本次变量替换
    if cached is not None:
        raw_templates, source_path = cached
        return _render_chat(raw_templates, variables, source_path)

    # 2.定位并读取 .chat.yaml
    base = _resolve_base(base_dir)
    path = base / f"{name}.chat.yaml"
    if not path.is_file():
        raise PromptNotFound(f"chat 模板不存在: {name}（查找 {path}）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PromptConfigurationError(f"chat 模板 YAML 非法 {path}: {exc}") from exc

    # 3.校验：format=chat + 非空 messages 列表
    if data.get("format", "chat") != "chat":
        raise PromptConfigurationError(f"{path} 不是 chat 格式（format != 'chat'）")
    msg_list = data.get("messages")
    if not isinstance(msg_list, list) or not msg_list:
        raise PromptConfigurationError(f"{path} 缺少非空 messages 列表")

    # 4.规整成 {role, content} 原始模板并缓存（缓存未渲染模板，非渲染结果）
    raw_templates: list[dict[str, str]] = []
    for msg in msg_list:
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        raw_templates.append({"role": msg.get("role", "user"), "content": content})
    _CHAT_CACHE[cache_key] = (raw_templates, str(path))

    # 5.渲染本次变量
    return _render_chat(raw_templates, variables, str(path))


def _render_chat(
    raw_templates: list[dict[str, str]],
    variables: Mapping[str, Any],
    source_path: str,
) -> list[BaseMessage]:
    """把 chat 原始模板逐条按 $var 渲染成 BaseMessage。

    参数：
        raw_templates: 未渲染的 {role, content} 列表
        variables: 占位符变量
        source_path: 出错提示用来源路径

    返回：
        System/Human 消息列表
    """
    messages: list[BaseMessage] = []
    for tmpl in raw_templates:
        # 1.每条正文用 string.Template 替换（JSON 的 {} 不受影响）
        try:
            content = Template(tmpl["content"]).substitute(dict(variables or {}))
        except (KeyError, ValueError) as exc:
            raise PromptConfigurationError(
                f"{source_path} 存在非法/缺失占位符（role={tmpl['role']!r}）: {exc}"
            ) from exc
        # 2.role 分流：system→SystemMessage，其余→HumanMessage
        if tmpl["role"] == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages
