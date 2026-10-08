from __future__ import annotations

from typing import Any

from harness.observability import context

"""HTTP trace 中间件（trace_middleware）——给每个请求生成 / 透传 trace_id。

    职责：每个 HTTP 请求进来时，从 X-Trace-Id 头透传（有则沿用）或新生成 trace_id，绑定到观测上下文
        run 创建链路据此把 trace_id 落到 runs / spans，实现「一次请求一条 trace」。

    对外暴露：
        - trace_middleware   FastAPI @app.middleware("http") 用的中间件函数
"""


async def trace_middleware(request: Any, call_next: Any) -> Any:
    """给请求绑定 trace 上下文，响应回写 X-Trace-Id。"""
    # 1.透传优先：上游带了 X-Trace-Id 就沿用（支持跨服务链路串联）
    inbound = request.headers.get("X-Trace-Id")
    trace_id = inbound or context.new_trace_id()
    # 2.绑定上下文后走请求（contextvar 同任务可见，run 创建链路能读到）
    with context.bind_trace(trace_id):
        response = await call_next(request)
    # 3.回写响应头，客户端 / 网关据此关联
    response.headers["X-Trace-Id"] = trace_id
    return response
