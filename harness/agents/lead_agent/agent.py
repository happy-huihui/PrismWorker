"""Lead Agent 组装：把模型、工具、中间件、提示词装配成一个可运行的 Agent。

核心函数 build_lead_agent(app_config, ...)：
    1. 按配置创建聊天模型（create_chat_model，支持再次覆盖模型名/思考模式）
    2. 组装工具集（沙箱工具 + 内置工具 + web 工具 + 记忆工具，按开关过滤）
    3. 挂载阶段 7 全套中间件（build_middlewares）
    4. 填充系统提示词（format_system_prompt）
    5. create_agent(...) 编译成 LangGraph 图（可传 checkpointer 持久化）

构造契约：
    agent = build_lead_agent(app_config, sandbox=sandbox, checkpointer=checkpointer)
    await agent.ainvoke({"messages": [HumanMessage(...)]}, config={"configurable": {"thread_id": ...}})
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from harness.agents.lead_agent.prompt import format_system_prompt
from harness.agents.middlewares import build_middlewares
from harness.agents.thread_state import ThreadState
from harness.config.app_config import AppConfig
from harness.models.factory import create_chat_model, supports_vision

logger = logging.getLogger(__name__)


def build_lead_agent(
    app_config: AppConfig,
    *,
    sandbox: Any | None = None,
    checkpointer: Any | None = None,
    model_name: str | None = None,
    thinking_enabled: bool | None = None,
    event_sink: Any | None = None,
    skills_dir: str | None = None,
) -> Any:
    """组装 Lead Agent 可运行图。

    参数：
        app_config        应用总配置（模型/工具/子代理等）
        sandbox           沙箱实例；None 时不注册沙箱文件/命令工具
        checkpointer      LangGraph 检查点器（如 AsyncSqliteSaver）；None 不持久化
        model_name        覆盖模型名；None 用配置激活模型
        thinking_enabled  是否开启思考模式；None 取配置（无则 False）
        event_sink        tool_progress 中间件的事件回调（思考链推前端用）
        skills_dir        技能目录（None → {base_dir}/skills）

    返回：
        langchain create_agent 编译后的图对象（await agent.ainvoke / astream）
    """
    _thinking = bool(thinking_enabled) if thinking_enabled is not None else False
    model = create_chat_model(
        name=model_name,
        thinking_enabled=_thinking,
        app_config=app_config,
    )

    tools = assemble_tools(app_config, sandbox=sandbox)

    middlewares = build_middlewares(
        app_config,
        event_sink=event_sink,
        skills_dir=skills_dir,
    )

    seen: set[str] = set()
    tool_names: list[tuple[str, str]] = []
    for tool in tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        desc = (tool.description or "").strip()
        tool_names.append((tool.name, desc))
    system_prompt = format_system_prompt(
        agent_name=app_config.agent_name,
        tool_names=tool_names,
        sandbox_enabled=sandbox is not None,
    )

    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middlewares,
        state_schema=ThreadState,
        checkpointer=checkpointer,
        name=app_config.agent_name,
    )

    logger.info(
        "Lead Agent 组装完成：模型=%s 工具=%d 个 中间件=%d 个 沙箱=%s",
        getattr(model, "model_name", "?"),
        len(tools),
        len(middlewares),
        "已连接" if sandbox is not None else "未连接",
    )
    return agent


def assemble_tools(
    app_config: AppConfig,
    *,
    sandbox: Any | None = None,
) -> list[BaseTool]:
    """按配置组装 Lead Agent 的全部可用工具（含开关与白名单过滤）。

    工具来源（四组）：
      1. 沙箱工具：read_file / write_file / glob_files / grep_files / list_dir / exec_command
         （sandbox 提供时注册；缺沙箱则跳过这一组）
      2. 内置工具：ask_clarification / list_uploaded_files / present_file /
         review_skill_package / task / view_image（视觉模型才注册）
      3. web 工具：web_search / web_fetch（按 tools 配置开关）
      4. 记忆工具：save_memory / search_memory / delete_memory（memory.mode=tool 时）

    所有工具最终再过一遍 app_config.is_tool_enabled 白名单过滤。
    """
    tools: list[BaseTool] = []

    if sandbox is not None:
        try:
            from harness.sandbox.tools import make_sandbox_tools

            tools.extend(make_sandbox_tools(sandbox, allow_bash=True))
        except Exception as exc:  # noqa: BLE001 —— 沙箱工具组装失败不影响其它工具
            logger.warning("沙箱工具组装失败，跳过沙箱工具组: %s", exc)

    from harness.tools.builtins import (
        ask_clarification_tool,
        list_uploaded_files,
        present_file_tool,
        review_skill_package,
        task_tool,
        view_image_tool,
    )

    tools.extend([
        list_uploaded_files,
        present_file_tool,
        review_skill_package,
        ask_clarification_tool,
        task_tool,
    ])
    if supports_vision(app_config=app_config):
        tools.append(view_image_tool)

    if app_config.tools.web_search.enabled:
        try:
            from harness.community.tavily_search.tools import web_search_tool

            tools.append(web_search_tool)
        except Exception as exc:  # noqa: BLE001 —— 依赖缺失时可注入性失败
            logger.warning("web_search 工具加载失败: %s", exc)
    if app_config.tools.web_fetch.enabled:
        try:
            from harness.community.web_fetch.tools import web_fetch_tool

            tools.append(web_fetch_tool)
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_fetch 工具加载失败: %s", exc)

    if app_config.memory.mode == "tool":
        try:
            from harness.memory.tools import (
                delete_memory_tool,
                save_memory_tool,
                search_memory_tool,
            )

            tools.extend([
                save_memory_tool,
                search_memory_tool,
                delete_memory_tool,
            ])
        except Exception as exc:  # noqa: BLE001
            logger.warning("记忆工具加载失败: %s", exc)

    filtered = [tool for tool in tools if app_config.is_tool_enabled(tool.name)]
    dropped = [tool.name for tool in tools if tool.name not in {t.name for t in filtered}]
    if dropped:
        logger.info("按配置停用的工具: %s", ", ".join(sorted(dropped)))
    return filtered