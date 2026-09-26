"""模型路由决策结果（decision）。

路由的产出不是「一个模型名字符串」，而是「模型名 + 为什么选它」。
把原因结构化带出来有两个用处：
    1. 运行层写进 run_meta 事件，前端思考链可以如实告诉用户「已自动选择 X（因为 Y）」；
    2. 排查「为什么这轮没升档」时不用加日志重跑，看事件流即可。

source 用枚举而非自由字符串，前端据此决定用哪套文案渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

"""决策来源。

    - "explicit"  调用方显式指定了模型名（如子代理固定用某模型），路由不干预
    - "vision"    输入含图片，改用了带视觉的模型
    - "keyword"   命中升档关键词，升到了推理模型
    - "default"   走默认档（最常见）
    - "fallback"  配置不完整 / 路由关闭，退回激活模型
"""
RoutingSource = Literal["explicit", "vision", "keyword", "default", "fallback"]


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """一次模型路由的完整决策。

    字段：
        model_name: 最终要用的模型名（对应 config.models 里的 name）；
            None 表示「交给工厂走激活模型」
        source: 决策来源（见 RoutingSource）
        reason: 人类可读的中文原因，直接可展示给用户
        matched_keyword: source == "keyword" 时命中的那个词（便于调试与展示）
        requested: 调用方原本显式请求的模型名（若有）
    """

    model_name: str | None
    source: RoutingSource
    reason: str
    matched_keyword: str | None = None
    requested: str | None = None

    @property
    def escalated(self) -> bool:
        """这次决策是否发生了「从默认档升到更高档」的偏移。"""
        return self.source in ("keyword", "vision")

    def to_meta(self) -> dict:
        """转成 run_meta 事件用的紧凑字典（字段名对齐前端 camelCase 之前的下划线风格）。"""
        return {
            "model_name": self.model_name or "",
            "source": self.source,
            "reason": self.reason,
            "matched_keyword": self.matched_keyword,
            "escalated": self.escalated,
        }


@dataclass(slots=True)
class RoutingSignals:
    """路由判定所需的输入信号（从 RunManager 的入参里抽取，便于单测）。

    字段：
        text: 本轮用户输入拼成的纯文本（多条消息时取最后一条 user 内容）
        has_images: 本轮输入是否含图片块
        thinking_enabled: 调用方是否显式开了深度思考
        explicit_model: 调用方显式指定的模型名
        message_count: 本轮输入消息条数（多轮拼接往往意味着长任务）
    """

    text: str = ""
    has_images: bool = False
    thinking_enabled: bool = False
    explicit_model: str | None = None
    message_count: int = 0
    extra_keywords: list[str] = field(default_factory=list)
