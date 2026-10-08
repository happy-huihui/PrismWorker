from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from harness.observability import context

"""span 数据模型与采集器（span）——结构化调用树的内存采集。

    职责：定义一次 agent 执行里的调用步骤 Span
            - llm
            - tool
            - retrieval
            - agent
          SpanCollector 全局单例，按 run_id 分桶将 span 存内存里
          SpanMiddleware 在模型/工具调用边界调用span的 start/finish
          run 收尾时 flush 落 spans 表。
          span 树通过 parent_span_id 表达嵌套层级，父指针取「当前 span」上下文（context.bind_span ）

    对外暴露：
        - SPAN_TYPE_LLM / TOOL / RETRIEVAL / AGENT   span 类型常量
        - Span                                      一次调用步骤
        - SpanCollector                             采集器（start_span / finish_span / drain / flush）
        - get_span_collector / reset_span_collector 采集器单例
"""

# span 类型：llm=模型调用，tool=工具执行，retrieval=记忆检索，agent=子代理
SPAN_TYPE_LLM = "llm"
SPAN_TYPE_TOOL = "tool"
SPAN_TYPE_RETRIEVAL = "retrieval"
SPAN_TYPE_AGENT = "agent"


@dataclass
class Span:
    """一次调用步骤（模型 / 工具 / 检索 / 子代理）。

    字段与 spans 表列一一对应；finished_at/duration_ms/token/cost 等由
    finish_span 回填，创建时只给 start 侧字段。
    """

    span_id: str
    trace_id: str
    run_id: str
    thread_id: str
    user_id: str
    type: str
    name: str
    started_at: float
    parent_span_id: str | None = None
    finished_at: float | None = None
    duration_ms: float | None = None
    model_name: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转成 spans 表一行的字段字典（键与 store.insert_span 严格对齐）。"""
        # created_at 直接用 started_at，避免额外字段漂移
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "type": self.type,
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "model_name": self.model_name,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
            "error": self.error,
            "attributes": self.attributes,
            "created_at": self.started_at,
        }


class SpanCollector:
    """进程级 span 采集器：按 run_id 分桶收集，run 收尾时 flush 落库。"""

    def __init__(self) -> None:
        """初始化；spans 桶 + 线程锁（并发多 run 各写各桶）。"""
        self._spans: dict[str, list[Span]] = {}
        self._lock = threading.Lock()

    def start_span(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        span_type: str,
        name: str,
        trace_id: str = "",
        parent_span_id: str | None = None,
        **fields: Any,
    ) -> Span:
        """新建一条 span 并登记到 run 桶（返回给调用方 finish 时回填）。

        参数：
            run_id / thread_id / user_id: 归属
            span_type / name: 类型与见名知意的名字（如 tool.web_search）
            trace_id: 归属 trace；空则从观测上下文取
            parent_span_id: 父步骤；None 表示挂在 run 根下
            fields: 其余 span 字段（model_name / input_tokens 等）透传

        返回：
            已登记的新 Span（started_at 已记，结束字段待 finish_span 回填）
        """
        span = Span(
            span_id=uuid.uuid4().hex,
            trace_id=trace_id or context.current_trace_id(),
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            type=span_type,
            name=name,
            started_at=time.time(),
            parent_span_id=parent_span_id,
            **fields,
        )
        with self._lock:
            self._spans.setdefault(run_id, []).append(span)
        return span

    def finish_span(self, span: Span, *, error: str | None = None, **fields: Any) -> None:
        """回填一条 span 的结束字段（耗时 / 用量 / 错误 / 附加字段）。

        参数：
            span: 待回填的 span
            error: 失败信息（成功为 None，不覆盖已有值）
            fields: 其余回填字段（model_name / input_tokens / cost 等）
        """
        span.finished_at = time.time()
        # 耗时 = 结束 - 开始，单位毫秒（单调性：异常路径也按当前时刻给近似值）
        span.duration_ms = round((span.finished_at - span.started_at) * 1000.0, 2)
        if error is not None:
            span.error = error
        for key, value in fields.items():
            setattr(span, key, value)

    def drain(self, run_id: str) -> list[dict[str, Any]]:
        """取出某 run 的全部 span 并清空（返回 to_dict 列表，供落库）。"""
        with self._lock:
            spans = self._spans.pop(run_id, [])
        # 按开始时间正序返回，保证落库后的树顺序稳定
        spans.sort(key=lambda s: s.started_at)
        return [s.to_dict() for s in spans]

    async def flush(self, run_id: str) -> list[dict[str, Any]]:
        """把某 run 的 span 落库，返回已落库的 span 列表（供 run 聚合 token/成本）。

        返回：
            已落库的 span 字典列表；无 span 返回空列表
        """
        spans = self.drain(run_id)
        if not spans:
            return []
        from harness.observability.store import ObservabilityStore

        store = ObservabilityStore()
        # 一次 run 的 span 量小，按需建连接写完即关，不做常驻连接
        await store.connect()
        try:
            for span in spans:
                await store.insert_span(span)
        finally:
            await store.close()
        return spans


_span_collector: SpanCollector | None = None
_span_collector_lock = threading.Lock()


def get_span_collector() -> SpanCollector:
    """返回进程级 SpanCollector 单例（懒构造，线程安全）。"""
    global _span_collector
    if _span_collector is not None:
        return _span_collector
    with _span_collector_lock:
        if _span_collector is None:
            _span_collector = SpanCollector()
        return _span_collector


def reset_span_collector() -> None:
    """清空单例（测试隔离用：下一个 get_span_collector 重新构造）。"""
    global _span_collector
    with _span_collector_lock:
        _span_collector = None
