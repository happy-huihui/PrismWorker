"""对话消息处理：把原始消息流清洗成「值得记忆」的输入。

职责（对应参考实现 message_processing 模块）：
    1. ``filter_messages_for_memory``：只保留用户消息与最终助手回复，
       剔除框架注入的 hidden 消息（hide_from_ui）、上传块占位消息与
       纯工具轮；
    2. ``filter_trivial``：剔除纯应声（「嗯 / ok / 好的 / 谢谢」整句
       匹配）的用户轮与对应回复，省下一次提取调用；
    3. ``detect_signals``：在最近若干用户消息上跑六类信号正则
       （correction / reinforcement / preference / identity / goal /
       decision），命中集合作为提取提示词 hint（事实强化入口）；
    4. ``load_patterns``：信号模式外部化（YAML 文件 + 编译缓存），
       可用 ``patterns_dir`` 覆盖内置 pattern/ 目录。

所有函数都是纯函数 + 标准消息对象（langchain_core.messages），不依赖
主链路其它模块，可独立单测。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from copy import copy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 上传块占位标签（被剥离后若整轮只剩标签则整轮剔除）
_UPLOAD_BLOCK_RE = re.compile(
    r"<(?P<tag>uploaded_files|current_uploads)>[\s\S]*?</(?P=tag)>\n*",
    re.IGNORECASE,
)

# 信号模式编译缓存：(name, patterns_dir) -> list[re.Pattern]
_PATTERN_CACHE: dict[tuple[str, str | None], list[re.Pattern[str]]] = {}

# 六类信号名（与 memory_update 提示词中的 hint 一一对应）
SIGNAL_NAMES: tuple[str, ...] = (
    "correction",
    "reinforcement",
    "preference",
    "identity",
    "goal",
    "decision",
)

# 纯应声判定的尾随字符（「ok.」「好的！」仍算应声）
_TRIVIAL_TRAIL = " \t\n\r.。,，!！?？;；"


def load_patterns(name: str, *, patterns_dir: str | None = None) -> list[re.Pattern[str]]:
    """加载并编译信号模式（YAML 列表；支持 {pattern, flags} 形态）。

    patterns_dir 为空时用内置 ``pattern/`` 目录；显式目录缺文件抛
    FileNotFoundError（配置错误要显眼），内置缺文件只告警（打包问题）。
    编译结果按 (name, patterns_dir) 缓存。
    """
    cache_key = (name, patterns_dir)
    cached = _PATTERN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    base = Path(patterns_dir) if patterns_dir else Path(__file__).parent / "pattern"
    path = base / f"{name}.yaml"
    if not path.exists():
        if patterns_dir is not None:
            raise FileNotFoundError(f"信号模式文件不存在: {path}")
        logger.warning("内置信号模式文件缺失 (%s)；%s 检测停用", path, name)
        _PATTERN_CACHE[cache_key] = []
        return []

    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
    except yaml.YAMLError as exc:
        raise ValueError(f"信号模式 YAML 非法 {path}: {exc}") from exc
    except OSError as exc:
        if patterns_dir is not None:
            raise OSError(f"读取信号模式失败 {path}: {exc}") from exc
        logger.warning("读取信号模式失败 %s: %s；%s 检测停用", path, exc, name)
        _PATTERN_CACHE[cache_key] = []
        return []

    if not isinstance(data, list):
        raise ValueError(f"信号模式文件 {path} 顶层必须是列表，实际是 {type(data).__name__}")

    compiled: list[re.Pattern[str]] = []
    for i, entry in enumerate(data):
        if isinstance(entry, str):
            pattern_text, flag_names = entry, []
        elif isinstance(entry, Mapping):
            pattern_text = entry.get("pattern")
            flag_names = entry.get("flags", []) or []
        else:
            logger.warning("跳过 %s 第 %d 项（非字符串非映射）", path, i)
            continue
        if not isinstance(pattern_text, str) or not pattern_text:
            logger.warning("跳过 %s 第 %d 项（缺 pattern）", path, i)
            continue
        flags = 0
        for flag_name in flag_names:
            if flag_name == "ignorecase":
                flags |= re.IGNORECASE
        try:
            compiled.append(re.compile(pattern_text, flags))
        except re.error as exc:
            raise ValueError(f"{path} 第 {i} 项正则非法: {exc}") from exc

    _PATTERN_CACHE[cache_key] = compiled
    return compiled


def extract_message_text(message: Any) -> str:
    """提取消息纯文本（兼容 str 与多模态块列表）。"""
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text_val = part.get("text")
                if isinstance(text_val, str):
                    parts.append(text_val)
        return " ".join(parts)
    return str(content)


def filter_messages_for_memory(messages: list[Any]) -> list[Any]:
    """清洗消息流：只保留用户输入与最终助手回复。

    规则：
        - human：剔除 hide_from_ui 的框架消息；剥离上传块占位（整轮只剩
          占位则剔除且跳过下一条 AI 回复）；
        - ai：只保留无 tool_calls 的最终回复（工具调用轮不记入记忆）。
    """
    filtered: list[Any] = []
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)

        if msg_type == "human":
            additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
            if additional_kwargs.get("hide_from_ui"):
                continue
            content_str = extract_message_text(msg)
            if (
                "<uploaded_files>" in content_str.lower()
                or "<current_uploads>" in content_str.lower()
            ):
                stripped = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
                if not stripped:
                    skip_next_ai = True
                    continue
                clean_msg = copy(msg)
                clean_msg.content = stripped
                filtered.append(clean_msg)
                skip_next_ai = False
            else:
                filtered.append(msg)
                skip_next_ai = False
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                if skip_next_ai:
                    skip_next_ai = False
                    continue
                filtered.append(msg)

    return filtered


def filter_trivial(
    messages: list[Any],
    *,
    patterns: list[re.Pattern[str]] | None = None,
) -> list[Any]:
    """剔除纯应声用户轮与其后回复（fullmatch，实质内容永不误删）。"""
    if patterns is None:
        patterns = load_patterns("trivial")
    if not patterns:
        return list(messages)

    result: list[Any] = []
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        if msg_type == "human":
            content = extract_message_text(msg).strip().rstrip(_TRIVIAL_TRAIL)
            is_trivial = bool(content) and any(
                pattern.fullmatch(content) for pattern in patterns
            )
            if is_trivial:
                skip_next_ai = True
                continue
            result.append(msg)
            skip_next_ai = False
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                if skip_next_ai:
                    skip_next_ai = False
                    continue
                result.append(msg)
    return result


def detect_signals(
    messages: list[Any],
    *,
    patterns_dir: str | None = None,
) -> set[str]:
    """在最近 6 条用户消息上检测六类信号，返回命中集合（可为空集）。"""
    recent_user_msgs = [
        msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"
    ]
    if not recent_user_msgs:
        return set()

    hits: set[str] = set()
    for name in SIGNAL_NAMES:
        patterns = load_patterns(name, patterns_dir=patterns_dir)
        if not patterns:
            continue
        for msg in recent_user_msgs:
            content = extract_message_text(msg).strip()
            if content and any(pattern.search(content) for pattern in patterns):
                hits.add(name)
                break
    return hits


def format_conversation_for_update(messages: list[Any]) -> str:
    """把消息序列渲染成提取提示词里的对话文本（用户/AI 交替行）。"""
    lines: list[str] = []
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        text = extract_message_text(msg).strip()
        if not text:
            continue
        role = "用户" if msg_type == "human" else "助手"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)