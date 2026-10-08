from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

"""观测上下文（context）——trace / run / span 三级关联 id 的进程级绑定。

    职责：维护当前线程的 trace_id / run_id / span_id / thread_id / user_id，
        trace_id = 一次 HTTP 请求；run_id = 一次 agent 执行；span_id = 一个步骤。
        供结构化日志 processor 与 span 采集器自动注入，业务代码零感知。
        

    对外暴露：
        - new_trace_id                      生成一个新 trace_id（TraceMiddleware 用）
        - bind_trace / bind_run / bind_span 进入/退出对应上下文的 contextmanager
        - current_trace_id / current_run_id / current_span_id 等读值
        - snapshot                          一次性取出全部观测字段（processor / span 用）
"""

# 三级 id + 归属字段各占一个 ContextVar：async 环境下天然隔离并发 Task 不串号
_TRACE_ID: ContextVar[str] = ContextVar("obs_trace_id", default="")
_RUN_ID: ContextVar[str] = ContextVar("obs_run_id", default="")
_SPAN_ID: ContextVar[str] = ContextVar("obs_span_id", default="")
_THREAD_ID: ContextVar[str] = ContextVar("obs_thread_id", default="")
_USER_ID: ContextVar[str] = ContextVar("obs_user_id", default="")


def new_trace_id() -> str:
    """生成一个新 trace_id（32 位 hex，请求级唯一）。"""
    return uuid.uuid4().hex


def current_trace_id() -> str:
    """读当前 trace_id（无上下文返回空串）。"""
    return _TRACE_ID.get()


def current_run_id() -> str:
    """读当前 run_id（无上下文返回空串）。"""
    return _RUN_ID.get()


def current_span_id() -> str:
    """读当前 span_id（无上下文返回空串）。"""
    return _SPAN_ID.get()


def current_thread_id() -> str:
    """读当前 thread_id（无上下文返回空串）。"""
    return _THREAD_ID.get()


def current_user_id() -> str:
    """读当前 user_id（无上下文返回空串）。"""
    return _USER_ID.get()


def snapshot() -> dict[str, str]:
    """一次性取出全部观测字段（结构化日志 processor / span 采集用）。

    返回：
        {trace_id, run_id, span_id, thread_id, user_id}，缺省均为空串
    """
    # 一次读全，避免调用方反复跨 contextvar 取值
    return {
        "trace_id": _TRACE_ID.get(),
        "run_id": _RUN_ID.get(),
        "span_id": _SPAN_ID.get(),
        "thread_id": _THREAD_ID.get(),
        "user_id": _USER_ID.get(),
    }


@contextmanager
def bind_trace(trace_id: str) -> Iterator[None]:
    """进入一次 HTTP 请求的 trace 上下文（TraceMiddleware 包裹请求用）。"""
    token = _TRACE_ID.set(trace_id or "")
    try:
        yield
    finally:
        _TRACE_ID.reset(token)


@contextmanager
def bind_run(*, trace_id: str, run_id: str, thread_id: str, user_id: str) -> Iterator[None]:
    """进入一次 agent 执行的观测上下文（run_worker 包裹 astream 用）。

    参数：
        trace_id / run_id / thread_id / user_id: 本次执行的四项归属
    """
    # 逐项 set 并记录 token，退出时按 token 复位，支持嵌套
    trace_token = _TRACE_ID.set(trace_id or "")
    run_token = _RUN_ID.set(run_id or "")
    thread_token = _THREAD_ID.set(thread_id or "")
    user_token = _USER_ID.set(user_id or "")
    try:
        yield
    finally:
        _TRACE_ID.reset(trace_token)
        _RUN_ID.reset(run_token)
        _THREAD_ID.reset(thread_token)
        _USER_ID.reset(user_token)


@contextmanager
def bind_span(span_id: str) -> Iterator[None]:
    """进入一个 span 步骤的上下文（SpanMiddleware 包裹模型/工具调用用）。"""
    token = _SPAN_ID.set(span_id or "")
    try:
        yield
    finally:
        _SPAN_ID.reset(token)
