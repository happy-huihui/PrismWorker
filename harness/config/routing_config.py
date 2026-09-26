from __future__ import annotations

from pydantic import BaseModel, Field

"""模型路由配置（routing）

    职责：定义「一轮对话该挑哪个模型」的规则参数。与 model_config.py 分工：
         - model_config.py   单个模型长什么样（怎么造）
         - routing_config.py 该挑哪一个（挑谁）

    设计要点：
        - 默认档 / 升级档都用「配置里的模型 name」指定，不硬编码 model id
          → 换供应商只改 models 段，路由规则不用动。
        - 关键词命中即升档：简单、可解释、零额外延迟，不引入第二个模型做意图识别。

    对外暴露：
        - DEFAULT_ESCALATE_KEYWORDS  默认升级关键词
        - RoutingConfig              路由规则配置
"""

# 默认升级关键词：只收「明确需要深度推理 / 长链路产出」的动词与场景词，
# 不收「写」「改」这类过宽泛的词（否则日常写作全被升档，反而变慢）
DEFAULT_ESCALATE_KEYWORDS: tuple[str, ...] = (
    # 深度分析与推理
    "深度分析",
    "深入分析",
    "详细分析",
    "逐步推理",
    "推理一下",
    "论证",
    "权衡",
    "对比分析",
    "根因",
    "定位问题",
    "排查",
    "debug",
    "调试",
    # 工程实施类（长链路、需要多步工具协作）
    "写代码",
    "写个代码",
    "实现一个",
    "重构",
    "架构设计",
    "设计方案",
    "设计一个",
    "写一个完整",
    "完整实现",
    "单元测试",
    "code review",
    "代码审查",
    # 复杂产出
    "生成报告",
    "写一份报告",
    "研究",
    "调研",
    "论文",
    "算法",
)


class RoutingConfig(BaseModel):
    """模型动态路由配置。

    缺省可用：enabled=True + default_model=None 时退化为「永远用激活模型」，
    因此老配置不写 routing 段也能正常跑。
    """

    enabled: bool = Field(default=True, description="是否启用动态路由")

    default_model: str | None = Field(
        default=None,
        description="默认档模型名（config.models 里的 name）；None 表示用 active_model",
    )

    escalate_model: str | None = Field(
        default=None,
        description="升级档模型名；None 表示不做升级（永远用默认档）",
    )

    escalate_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ESCALATE_KEYWORDS),
        description="命中即升档的关键词（大小写不敏感的子串匹配）",
    )

    min_chars_for_escalation: int = Field(
        default=8,
        description=(
            "输入短于该字符数时不升档（0 表示不限）；"
            "用于避免「顺便深度分析一下」这种一句话被误升档"
        ),
    )

    vision_model: str | None = Field(
        default=None,
        description=(
            "带图片输入时改用的模型名（需要有 supports_vision）；"
            "None 表示带图也不换模型"
        ),
    )
