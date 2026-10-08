from __future__ import annotations

import asyncio
import logging
from typing import Any

"""评测 Agent 装配（agent）——与线上完全同构的全真链路装配。

    职责：复刻 harness.runtime.runs.worker 的装配路径（create_checkpointer +
        AgentRegistry.build_agent + get_app_sandbox），返回与线上同构的 agent，
        供离线评测目标函数调用。评测测的就是「真实链路」，因此这里**不做任何降级**：
        - 沙箱：走 get_app_sandbox（SandboxManager，docker 容器），与线上同源
        - checkpointer：create_checkpointer（AsyncSqliteSaver），与线上同源
        - 中间件 / 工具 / 提示词：registry.build_agent 内全量装配，与线上同源

    对外暴露：
        - build_eval_agent   异步装配，返回 (agent, sandbox, checkpointer)
"""

logger = logging.getLogger(__name__)


async def build_eval_agent(
    *,
    model_name: str | None = None,
    thinking_enabled: bool = False,
    thread_id: str = "eval",
    user_id: str = "eval",
) -> tuple[Any, Any, Any]:
    """异步装配全真 agent（沙箱 + checkpointer + 全中间件/工具），与线上同构。

    参数：
        model_name: 覆盖模型档名；None 走配置默认路由
        thinking_enabled: 是否思考模式
        thread_id / user_id: 沙箱 provider 的确定性归属（默认 eval）

    返回：
        (agent, sandbox, checkpointer)；agent 可直接 await agent.ainvoke(...)
    """
    # 1.设置运行时线程/用户上下文：沙箱 provider 据此确定性取/建容器
    from harness.runtime.sandbox import set_runtime_thread_id, set_runtime_user_id

    set_runtime_thread_id(thread_id)
    set_runtime_user_id(user_id)

    # 2.checkpointer：与线上同源（AsyncSqliteSaver，落 checkpoints.db）
    from harness.memory.short_term import create_checkpointer

    checkpointer = await create_checkpointer()

    # 3.注册表：绑定沙箱 provider（get_app_sandbox），与 build_run_service 同构
    from harness.config.app_config import get_app_config
    from harness.runtime.graph import get_agent_registry
    from harness.runtime.sandbox import get_app_sandbox

    registry = get_agent_registry(
        app_config=get_app_config(),
        sandbox_provider=get_app_sandbox,
    )

    # 4.装配移出事件循环：build_agent 内做 docker subprocess 探测 +
    #    wait_for_sandbox_ready 的 time.sleep 轮询（冷启动最长 120s），
    #    在事件循环里直调会冻结调用方（与 worker 的 asyncio.to_thread 同因）
    def _assemble() -> tuple[Any, Any]:
        agent = registry.build_agent(
            model_name=model_name,
            thinking_enabled=thinking_enabled,
            checkpointer=checkpointer,
        )
        # 沙箱实例不可缓存（活跃/热池状态机），每次向 provider 取一次
        return agent, registry.sandbox

    agent, sandbox = await asyncio.to_thread(_assemble)
    logger.info(
        "评测 agent 装配完成：模型=%s 沙箱=%s",
        model_name or "auto",
        "已连接" if sandbox is not None else "未连接",
    )
    return agent, sandbox, checkpointer
