"""路由信号抽取与规则判定（rules）。

职责划分：
    - signals.py（本模块内的 build_signals）：把「这一轮的输入消息」翻译成
      RoutingSignals（纯文本 / 有无图片 / 是否显式指定模型）。
    - 本模块的规则函数：逐条判断信号，返回可选的 RoutingDecision。

为什么把「抽信号」也放这儿而不是丢进 router.py：
    抽信号是纯函数、可单测，且规则本身要靠它；两者放一起，改规则时
    不用跨文件跳。router.py 只负责「读配置 → 按顺序试规则 → 兜底」。

规则顺序即优先级，先命中先返回：
    1. explicit  调用方显式指定 → 不干预
    2. vision    含图片 且 配了 vision_model → 换视觉模型
    3. keyword   命中升档关键词 且 达到最小长度 → 升推理模型
    4. default   走默认档
"""

from __future__ import annotations

from typing import Any

from harness.config.routing_config import RoutingConfig
from harness.models.routing.decision import RoutingDecision, RoutingSignals

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
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    if isinstance(block, str) and block:
                        text_parts.append(block)
                    continue
                block_type = block.get("type")
                if block_type in _IMAGE_BLOCK_TYPES:
                    has_images = True
                    continue
                if block_type == "text":
                    value = block.get("text")
                    if isinstance(value, str) and value:
                        text_parts.append(value)
        elif isinstance(content, str) and content.strip():
            text_parts.append(content)

    # 只取最后一段非空文本作为「本轮诉求」
    text = ""
    for part in reversed(text_parts):
        if part.strip():
            text = part
            break

    # 剥离防注入包裹标记，避免标记里的词（如 BEGIN）误命中关键词
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
    if not text:
        return None
    lowered = text.lower()
    for keyword in sorted(keywords, key=len, reverse=True):
        if keyword and keyword.lower() in lowered:
            return keyword
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
    if not config.escalate_model:
        return None
    # 太短的输入不升档（防止「深度分析下」这种一句话把整轮拖慢）
    if config.min_chars_for_escalation and len(signals.text) < config.min_chars_for_escalation:
        return None
    hit = match_keyword(signals.text, config.escalate_keywords)
    if not hit:
        return None
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
