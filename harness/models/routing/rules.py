from __future__ import annotations

from typing import Any

from harness.config.routing_config import RoutingConfig
from harness.models.routing.decision import RoutingDecision, RoutingSignals

"""路由信号抽取与规则判定

    职责：把本轮输入翻译成路由信号，并按优先级逐条判定用哪个模型
        - build_signals  抽信号（纯函数、可单测）
        - rule_*         四条规则，命中即返回

    对外暴露：
        - build_signals   从本轮输入消息抽取 RoutingSignals
        - match_keyword   返回文本里第一个命中的升档关键词
        - rule_explicit / rule_vision / rule_keyword / rule_default
                          四条规则，由 router.py 按顺序调用（顺序即优先级）
"""

"""图片内容块的类型标记（OpenAI / Anthropic 两套命名都收）。

LangChain 在序列化为请求体时用 "image_url"；部分 provider 保留
"image" / "input_image"。这里只做「有没有图」的存在性判断，不解析内容。
"""
_IMAGE_BLOCK_TYPES = frozenset({"image_url", "image", "input_image", "image_base64"})


def build_signals(
    messages: Any,
    *,
    explicit_model: str | None = None,
    thinking_enabled: bool = False,
) -> RoutingSignals:
    """从本轮输入消息里抽取路由信号。

    参数：
        messages: 本轮输入消息（dict 或 BaseMessage 混合序列）
        explicit_model: 调用方显式指定的模型名
        thinking_enabled: 调用方是否显式开了深度思考

    返回：
        RoutingSignals；文本取「最后一条含文本的消息」——多轮拼接时
        最后一条才是用户这次的诉求，用首条会把上一轮的意图带进来
    """
    # 1.逐条扫描输入消息，收集文本片段与图片标记
    text_parts: list[str] = []
    has_images = False
    count = 0

    for message in messages or []:
        count += 1
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        # 1.1 多模态块列表：区分图片块与文本块
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    if isinstance(block, str) and block:
                        text_parts.append(block)
                    continue
                block_type = block.get("type")
                # 命中图片类型标记即置 has_images
                if block_type in _IMAGE_BLOCK_TYPES:
                    has_images = True
                    continue
                # 文本块：非空才收集
                if block_type == "text":
                    value = block.get("text")
                    if isinstance(value, str) and value:
                        text_parts.append(value)
        # 1.2 纯字符串内容：非空才收集
        elif isinstance(content, str) and content.strip():
            text_parts.append(content)

    # 2.只取最后一段非空文本作为「本轮诉求」（多轮拼接时首条是上一轮意图）
    text = ""
    for part in reversed(text_parts):
        if part.strip():
            text = part
            break

    # 3.剥离防注入包裹标记，避免标记里的词（如 BEGIN）误命中关键词
    from harness.runtime.serialization import strip_user_input_wrapper

    return RoutingSignals(
        text=strip_user_input_wrapper(text),
        has_images=has_images,
        thinking_enabled=bool(thinking_enabled),
        explicit_model=explicit_model,
        message_count=count,
    )


def match_keyword(text: str, keywords: list[str]) -> str | None:
    """在文本里找第一个命中的升档关键词（大小写不敏感子串匹配）。

    参数：
        text: 已剥离包裹标记的用户文本
        keywords: 配置里的关键词表

    返回：
        命中的关键词原文；无命中返回 None

    说明：按关键词长度降序试，保证「深度分析」先于「分析」命中，
        这样 reason/matched_keyword 展示的是更精确的那个词。
    """
    # 1.空文本无关键词可命中
    if not text:
        return None
    # 2.统一转小写做大小写不敏感匹配
    lowered = text.lower()
    # 3.按关键词长度降序试，保证「深度分析」先于「分析」命中
    for keyword in sorted(keywords, key=len, reverse=True):
        if keyword and keyword.lower() in lowered:
            return keyword
    # 4.无命中
    return None


def rule_explicit(
    signals: RoutingSignals,
    config: RoutingConfig,
) -> RoutingDecision | None:
    """规则 1：调用方显式指定模型名 → 尊重它，路由不干预。"""
    if not signals.explicit_model:
        return None
    return RoutingDecision(
        model_name=signals.explicit_model,
        source="explicit",
        reason=f"调用方显式指定模型 {signals.explicit_model}",
        requested=signals.explicit_model,
    )


def rule_vision(
    signals: RoutingSignals,
    config: RoutingConfig,
) -> RoutingDecision | None:
    """规则 2：输入含图片且配置了视觉模型 → 换用视觉模型。

    注意：这里不校验 vision_model 是否真的 supports_vision（工厂会在
    构造时按能力表处理）；配置写错时降级为「仍用默认档」而非报错。
    """
    if not signals.has_images or not config.vision_model:
        return None
    return RoutingDecision(
        model_name=config.vision_model,
        source="vision",
        reason=f"本轮输入包含图片，改用支持视觉的模型 {config.vision_model}",
    )


def rule_keyword(
    signals: RoutingSignals,
    config: RoutingConfig,
) -> RoutingDecision | None:
    """规则 3：命中升档关键词 → 升到推理模型（未配升级模型则不升）。"""
    # 1.未配置升级模型 → 不升档
    if not config.escalate_model:
        return None
    # 2.太短的输入不升档（防止「深度分析下」这种一句话把整轮拖慢）
    if config.min_chars_for_escalation and len(signals.text) < config.min_chars_for_escalation:
        return None
    # 3.找关键词命中
    hit = match_keyword(signals.text, config.escalate_keywords)
    if not hit:
        return None
    # 4.命中即升档
    return RoutingDecision(
        model_name=config.escalate_model,
        source="keyword",
        reason=f"输入涉及「{hit}」，自动切换到更强的推理模型 {config.escalate_model}",
        matched_keyword=hit,
    )


def rule_default(
    signals: RoutingSignals,
    config: RoutingConfig,
) -> RoutingDecision:
    """规则 4（兜底）：走默认档。"""
    model_name = config.default_model
    if model_name:
        reason = f"使用默认模型 {model_name}"
    else:
        model_name = None
        reason = "使用系统激活模型"
    return RoutingDecision(model_name=model_name, source="default", reason=reason)


# 规则表：顺序即优先级（显式 > 视觉 > 关键词 > 默认）
# 每条规则签名统一为 (signals, config) -> RoutingDecision | None，
# 便于后续插入新规则（如「按上下文长度升档」）而不用改 router 逻辑。
RULES = (rule_explicit, rule_vision, rule_keyword)
