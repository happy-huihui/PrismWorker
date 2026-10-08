from __future__ import annotations

import logging
from typing import Any

from harness.agents.lead_agent.agent import build_lead_agent
from harness.config.app_config import get_app_config

"""评测用图导出（graph）——给 langgraph dev（LangGraph Studio）加载的可视化图。

    职责：在模块顶层暴露 `graph`，供 langgraph.json 的 graphs 指向，让
        `langgraph dev` 能在 Studio 里交互式调试图结构与轻量跑跑（web/内置工具）。
        ⚠️ 这是「可视化调试」入口，**不是评测入口**：
        - langgraph dev 要求模块顶层同步可导入的图，而线上沙箱（docker）+ 异步
          checkpointer 无法在 import 时装配，故此图无沙箱、无 checkpointer。
        - 真正的「全真链路」离线评测走 `python -m harness.eval`（见 __main__），
          那里用 harness.eval.agent 与线上同构装配（沙箱 + checkpointer + 全中间件）。

    对外暴露：
        - graph   langchain create_agent 编译后的图（CompiledStateGraph，无沙箱预览）
"""

logger = logging.getLogger(__name__)


def _build_graph() -> Any:
    """装配无沙箱、无 checkpointer 的 Lead Agent 图（同步，供模块顶层一次性构建）。"""
    # 1.取全局配置：config.yaml + .env 已由 langgraph dev / runner 提前加载
    config = get_app_config()
    # 2.无沙箱、无 checkpointer → build_lead_agent 同步返回编译图
    #   （沙箱类工具不注册，web/内置工具仍可用，适合先跑 QA/分析类评测）
    return build_lead_agent(config, sandbox=None, checkpointer=None)


# 模块顶层暴露 graph：langgraph.json 里 graphs.lead_agent 指向这个变量
graph = _build_graph()
