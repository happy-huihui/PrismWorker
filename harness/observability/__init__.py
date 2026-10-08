from __future__ import annotations
from harness.observability import context  # noqa: F401  观测上下文
from harness.observability.cost import compute_cost, lookup_pricing  # noqa: F401  成本
from harness.observability.logging_setup import (  # noqa: F401  日志装配
    get_observability_logger,
    setup_observability_logging,
)
from harness.observability.span import (  # noqa: F401  span 采集
    Span,
    SpanCollector,
    get_span_collector,
    reset_span_collector,
)
from harness.observability.store import ObservabilityStore  # noqa: F401  落库仓库

"""观测子系统（observability）——结构化日志 + 观测链路的地基包。

    职责：为 PrismWorker 提供「结构化日志 + trace/run/span 三级关联 + 落库 + 成本」。
        本包不侵入既有业务逻辑：日志装配用 structlog 的 ProcessorFormatter 把根日志
        无痛升级；trace/run/span 上下文由 context 包维护，业务代码零感知。

    对外暴露：
        - setup_observability_logging  配置根日志（控制台 pretty + 文件 JSON）
        - get_observability_logger     取结构化 logger（新代码埋点用）
        - context                       观测上下文（trace/run/span 绑定与读取）
        - Span / SpanCollector          span 采集（get_span_collector 单例）
        - ObservabilityStore           spans / logs 表仓库（扩展现有 runs 库）
        - compute_cost / lookup_pricing 成本计算与模型价格表
"""

__all__ = [
    "setup_observability_logging",
    "get_observability_logger",
    "context",
    "Span",
    "SpanCollector",
    "get_span_collector",
    "reset_span_collector",
    "ObservabilityStore",
    "compute_cost",
    "lookup_pricing",
]
