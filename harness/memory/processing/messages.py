from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from copy import copy
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

"""对话消息清洗（processing.messages）

    职责：把原始消息流清洗成「值得记忆」的输入，属于无状态纯变换层。
    内容：
        1. filter_messages_for_memory  只留用户输入 + 最终助手回复（剔除 hidden/上传占位/纯工具轮）
        2. filter_trivial              剔除纯应声用户轮（「嗯/ok/谢谢」）及其回复
        3. detect_signals              在最近用户消息上跑六类信号正则，产出提取 hint
        4. extract_message_text        消息 content → 纯文本
        5. format_conversation_for_update  把对话渲染成「用户: … / 助手: …」文本
        6. load_patterns               信号模式外部化（YAML + 编译缓存）

    说明：全部为纯函数，只依赖标准消息对象，可独立单测；
         内置信号模式目录固定在 harness/memory/pattern/。
"""

# 上传块占位标签（被剥离后若整轮只剩标签则整轮剔除）
_UPLOAD_BLOCK_RE = re.compile(
    r"<(?P<tag>uploaded_files|current_uploads)>[\s\S]*?</(?P=tag)>\n*",
    re.IGNORECASE,
)

# 信号模式编译缓存：(name, patterns_dir) -> list[re.Pattern]，避免重复读盘编译
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

# 纯应声判定的尾随字符（「ok.」「好的！」去掉标点后仍算应声）
_TRIVIAL_TRAIL = " \t\n\r.。,，!！?？;；"


def load_patterns(name: str, *, patterns_dir: str | None = None) -> list[re.Pattern[str]]:
    """加载并编译信号模式（YAML 列表；支持 {pattern, flags} 形态）。

    参数：
        name: 模式名（correction/trivial/… 之一，对应 pattern/{name}.yaml）
        patterns_dir: 外部模式目录；None 用内置 harness/memory/pattern

    返回：
        编译好的正则列表（内置缺文件时返回空表，只告警）

    异常：
        显式 patterns_dir 下文件缺失抛 FileNotFoundError；YAML/正则非法抛错

    说明：结果按 (name, patterns_dir) 缓存；显式目录缺文件要显眼报错，
         内置目录缺文件只告警停用（打包容错）。
    """
    # 1.先查缓存，命中直接返回
    cache_key = (name, patterns_dir)
    cached = _PATTERN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # 2.定位模式文件：显式目录优先，否则内置 pattern/（本文件在 processing/ 下，故上退一级）
    base = Path(patterns_dir) if patterns_dir else Path(__file__).resolve().parent.parent / "pattern"
    path = base / f"{name}.yaml"
    # 3.文件缺失：显式目录=配置错误要抛；内置=只告警停用并缓存空表
    if not path.exists():
        if patterns_dir is not None:
            raise FileNotFoundError(f"信号模式文件不存在: {path}")
        logger.warning("内置信号模式文件缺失 (%s)；%s 检测停用", path, name)
        _PATTERN_CACHE[cache_key] = []
        return []

    # 4.读 YAML：显式目录读失败直接抛，内置读失败告警停用
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

    # 5.顶层必须是列表（每项是正则字符串或 {pattern,flags} 映射）
    if not isinstance(data, list):
        raise ValueError(f"信号模式文件 {path} 顶层必须是列表，实际是 {type(data).__name__}")

    # 6.逐项编译：字符串=无 flag；映射=取 pattern + flags(ignorecase)
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
        # 7.把 flag 名翻译成 re 常量
        flags = 0
        for flag_name in flag_names:
            if flag_name == "ignorecase":
                flags |= re.IGNORECASE
        # 8.编译，非法正则直接抛（配置错误要显眼）
        try:
            compiled.append(re.compile(pattern_text, flags))
        except re.error as exc:
            raise ValueError(f"{path} 第 {i} 项正则非法: {exc}") from exc

    # 9.写入缓存并返回
    _PATTERN_CACHE[cache_key] = compiled
    return compiled


def extract_message_text(message: Any) -> str:
    """提取消息纯文本（兼容 str 与多模态块列表）。

    参数：
        message: 标准消息对象（取 .content）

    返回：
        拼接后的纯文本
    """
    content = getattr(message, "content", "")
    # 多模态列表：str 段与 text 块都用空格串起来
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
    # 普通字符串内容直接返回
    return str(content)


def filter_messages_for_memory(messages: list[Any]) -> list[Any]:
    """清洗消息流：只保留用户输入与最终助手回复。

    参数：
        messages: 原始消息序列

    返回：
        清洗后的消息列表

    规则：
        - human：剔除 hide_from_ui 的框架消息；剥离上传块占位（整轮只剩
          占位则剔除且跳过下一条 AI 回复）；
        - ai：只保留无 tool_calls 的最终回复（工具调用轮不记入记忆）。
    """
    filtered: list[Any] = []
    # 上传占位整轮被剔除时，用它标记「下一条 AI 回复也一并跳过」
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)

        # 1.用户轮
        if msg_type == "human":
            additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
            # 1.1 框架注入的隐藏消息不进记忆
            if additional_kwargs.get("hide_from_ui"):
                continue
            content_str = extract_message_text(msg)
            # 1.2 含上传块：先剥离占位标签，剥空则整轮剔除并标记跳下一条 AI
            if (
                "<uploaded_files>" in content_str.lower()
                or "<current_uploads>" in content_str.lower()
            ):
                stripped = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
                if not stripped:
                    skip_next_ai = True
                    continue
                # 1.3 剥完仍有实质内容：复制一份替换为干净文本再保留
                clean_msg = copy(msg)
                clean_msg.content = stripped
                filtered.append(clean_msg)
                skip_next_ai = False
            else:
                # 1.4 无上传块：原样保留
                filtered.append(msg)
                skip_next_ai = False
        # 2.助手轮：只保留无工具调用的最终回复
        elif msg_type == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                # 2.1 上一条是被剔除的上传占位，则这条回复也跳过
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
    """剔除纯应声用户轮与其后回复（fullmatch，实质内容永不误删）。

    参数：
        messages: 已初筛的消息
        patterns: 应声正则；None 时用内置 trivial 模式

    返回：
        去掉「嗯/ok/谢谢」这类无信息用户轮及其回复后的消息
    """
    # 1.缺省加载内置 trivial 模式
    if patterns is None:
        patterns = load_patterns("trivial")
    # 2.没有模式则原样返回（不改变行为）
    if not patterns:
        return list(messages)

    result: list[Any] = []
    # 命应用户轮时，用它跳过紧随其后的 AI 回复
    skip_next_ai = False
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        # 1.用户轮：去掉尾随标点后做 fullmatch，命中即当纯应声剔除
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
        # 2.助手轮：只处理无工具调用的最终回复；被标记则跳过
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
    """在最近 6 条用户消息上检测六类信号，返回命中集合（可为空集）。

    参数：
        messages: 清洗后的消息
        patterns_dir: 外部模式目录；None 用内置

    返回：
        命中的信号名集合（作为提取提示词 hint）
    """
    # 1.只看最近 6 条里的用户消息
    recent_user_msgs = [
        msg for msg in messages[-6:] if getattr(msg, "type", None) == "human"
    ]
    if not recent_user_msgs:
        return set()

    # 2.逐类信号：任一用户消息命中即记录该类（每类命中一次即停）
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
    """把消息序列渲染成提取提示词里的对话文本（用户/AI 交替行）。

    参数：
        messages: 清洗后的消息

    返回：
        形如「用户: …\n助手: …」的多行文本
    """
    lines: list[str] = []
    for msg in messages:
        msg_type = getattr(msg, "type", None)
        text = extract_message_text(msg).strip()
        # 空文本轮跳过，不产生空行
        if not text:
            continue
        # human→「用户」、其余→「助手」
        role = "用户" if msg_type == "human" else "助手"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)
