from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.tools.types import Runtime

"""子代理派发工具

    职责：把有界任务派给子代理，在独立上下文里执行后取回结果
        - 延迟导入 executor（模块未就绪时回中文错误，而不是 ImportError）
        - 按 subagent_type 查注册表；未知类型回可用列表
        - 子代理不更新 todo，任务清单只由主线维护

    对外暴露：
        - task_tool   工具本体
"""

logger = logging.getLogger(__name__)


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """把一个有界任务派发给专门的子代理在自己的上下文中执行。

    什么情况下用 task 工具：
    - 独立的大块工作：能并行跑、明显缩短整体耗时
    - 需要专业工具/技能/模型的任务：子代理有主代理没有的能力
    - 需要上下文隔离的调研：内容多、占上下文，派出去更干净

    与任务清单（todo）的配合（重要）：
    - 子代理不会更新你的任务清单，todo 只由你在主线维护。
    - 派发前：把对应条目的状态更新为 in_progress（多个并行派发可同时标）。
    - 收到结果后：立即把已完成条目标 completed；做不完/不再需要的标 cancelled。
      状态必须如实——run 结束时系统会把仍挂着的 in_progress 归位为 cancelled。

    什么情况下不要用 task 工具：
    - 只是复杂、多步骤、耗时的任务（谜思：先算清楚「子代理完成它
      是否会更快/更省」再决定；多数时候直接在主线做完更便宜）
    - 多个子任务有依赖、共享可变状态或有外部副作用
    - 需要用户交互或澄清的任务

    成本提醒：
    - 子代理会重复发现仓库信息、产生协调成本，别为「显得专业」派发
    - 主线能用现成工具快速搞定的，就地解决

    Args:
        description: 任务的一句话描述，用于日志/进度展示（先说它）。
        prompt: 给子代理的完整任务指令，写清楚要做什么、期望的输出。
        subagent_type: 子代理类型。当前内置类型：
            - general_purpose: 通用子代理（复用主模型 + 精简工具集）
            若传入未知类型，将返回可用类型列表。
    """
    # 1.延迟导入：子代理模块未就绪时给明确错误，而不是 ImportError 炸掉
    try:
        from harness.subagents.executor import SubagentExecutor
        from harness.subagents.builtins import (
            get_available_subagent_names,
            get_subagent_config,
        )
    except ImportError as exc:
        logger.warning("task 工具调用时子代理模块不可用: %s", exc)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        "错误：子代理执行器尚未启用（subagents/executor 未实现）。"
                        "请告知系统管理员或稍后再试。",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # 2.查注册表；未知类型回可用列表，让模型自己改
    config = get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(get_available_subagent_names())
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"错误：未知的子代理类型 '{subagent_type}'。可用类型: {available}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # 3.从 runtime 收集执行器需要的上下文（父模型 / 沙箱 / 线程 / 用户）
    executor_kwargs: dict[str, Any] = {
        "config": config,
        "parent_model": _parent_model_name(runtime),
        "sandbox_state": _runtime_value(runtime, "sandbox"),
        "thread_data": _runtime_value(runtime, "thread_data"),
        "thread_id": _thread_id(runtime),
        "user_id": _user_id(runtime),
    }
    try:
        executor = SubagentExecutor(**executor_kwargs)
    except Exception as exc:
        logger.error("task 工具创建子代理执行器失败: %s", exc)
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"错误：无法创建子代理执行器: {type(exc).__name__}: {exc}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # 4.执行并把结果转成统一 ToolMessage
    result = await executor.execute(prompt)
    return _result_command(tool_call_id=tool_call_id, result=result)


def _result_command(*, tool_call_id: str, result: Any) -> Command:
    """把子代理结果转成统一 ToolMessage（接口壳：约定字段名供阶段5对齐）。"""
    status = getattr(result, "status", None)
    # completed（或没有 status）当成功；其余当失败并带上 error
    if status in ("completed", None):
        body = getattr(result, "result", None) or "子代理任务已完成。"
        return Command(
            update={
                "messages": [
                    ToolMessage(content=str(body), tool_call_id=tool_call_id, name="task")
                ]
            }
        )
    error = getattr(result, "error", None) or f"子代理任务状态: {status}"
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"错误：子代理任务未成功（{status}）。{error}",
                    tool_call_id=tool_call_id,
                    name="task",
                )
            ]
        }
    )


def _runtime_value(runtime: Runtime, key: str) -> Any:
    """从 runtime 状态取值（无状态返回 None）。"""
    # runtime 没有 state（独立调用）时返回 None
    state = getattr(runtime, "state", None)
    if state is None:
        return None
    return state.get(key)


def _parent_model_name(runtime: Runtime) -> str | None:
    """从 runtime 元数据里取父模型名（供子代理模型继承）。"""
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        # 父模型名放在 config.metadata.model_name，供子代理 inherit
        metadata = config.get("metadata") or {}
        if isinstance(metadata, dict):
            return metadata.get("model_name")
    return None


def _thread_id(runtime: Runtime) -> str | None:
    """从 runtime 上下文或 config 里解析线程 ID。"""
    # 1.先 runtime.context
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        thread_id = context.get("thread_id")
        if thread_id:
            return str(thread_id)
    # 2.再 config.configurable
    config = getattr(runtime, "config", None)
    if isinstance(config, dict):
        configurable = config.get("configurable") or {}
        thread_id = configurable.get("thread_id")
        if thread_id:
            return str(thread_id)
    return None


def _user_id(runtime: Runtime) -> str | None:
    """从 runtime 解析用户 ID（复用现有工具函数）。"""
    from harness.runtime.user_context import resolve_runtime_user_id

    return resolve_runtime_user_id(runtime)
