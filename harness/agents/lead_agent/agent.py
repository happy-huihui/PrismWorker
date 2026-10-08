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

"""Lead Agent 组装

    职责：把模型、工具、中间件、提示词装配成一个可运行的 Agent
        - build_lead_agent  装配五步：建模型 → 组工具 → 挂中间件 → 填提示词 → 编译成图
        - assemble_tools    按开关组装工具集（沙箱 + 内置 + web + 记忆）

    对外暴露：
        - build_lead_agent(app_config, *, sandbox=..., checkpointer=..., ...)
        - assemble_tools(...)
"""

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
    # 1.思考开关：显式传入优先；未传按关闭处理（配置层的思考开关由模型路由决定）
    _thinking = bool(thinking_enabled) if thinking_enabled is not None else False
    # 2.按配置造模型（模型名与思考模式都允许调用方覆盖）
    model = create_chat_model(
        name=model_name,
        thinking_enabled=_thinking,
        app_config=app_config,
    )

    # 3.组装工具集（沙箱 + 内置 + web + 生图 + 记忆，逐组按开关过滤）
    tools = assemble_tools(app_config, sandbox=sandbox)

    # 4.组装中间件（把沙箱传进去，供「写前读」护栏探测目标文件是否存在）
    middlewares = build_middlewares(
        app_config,
        event_sink=event_sink,
        skills_dir=skills_dir,
        # 传沙箱给中间件：「写前读」护栏用它探测目标是否存在（不存在即新建 → 放行）
        sandbox=sandbox,
    )

    # 5.工具去重后取 (名称, 一句话说明)：系统提示词里的「可用工具」段要用
    seen: set[str] = set()
    tool_names: list[tuple[str, str]] = []
    for tool in tools:
        # 同名工具只登记一次（重复注册时以先出现的为准）
        if tool.name in seen:
            continue
        seen.add(tool.name)
        desc = (tool.description or "").strip()
        tool_names.append((tool.name, desc))
    # 6.填模板生成系统提示词（沙箱是否可用决定用哪一段路径约定）
    system_prompt = format_system_prompt(
        agent_name=app_config.agent_name,
        tool_names=tool_names,
        sandbox_enabled=sandbox is not None,
        app_config=app_config,
    )

    # 7.编译成 LangGraph 图：state_schema 固定用 ThreadState，checkpointer 决定是否持久化
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middlewares,
        state_schema=ThreadState,
        checkpointer=checkpointer,
        name=app_config.agent_name,
    )

    # 8.打一条组装结果日志：排查「工具/中间件没生效」时先看这里
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

    工具来源（五组）：
      1. 沙箱工具：read_file / write_file / glob_files / grep_files / list_dir / exec_command
         （sandbox 提供时注册；缺沙箱则跳过这一组）
      2. 内置工具：ask_clarification / list_uploaded_files / present_file /
         review_skill_package / task / view_image（视觉模型才注册）
      3. web 工具：web_search / web_fetch（按 tools 配置开关）
      4. 生成工具：generate_image（火山方舟豆包生图，按 tools.ark_image.enabled 开关）
      5. 记忆工具：save_memory / search_memory / delete_memory（memory.mode=tool 时）

    所有工具最终再过一遍 app_config.is_tool_enabled 白名单过滤。
    """
    tools: list[BaseTool] = []

    # 1.沙箱工具组（read_file / write_file / glob / grep / list_dir / exec_command）
    #   没有沙箱实例就整组跳过 —— 模型因此不具备文件与命令能力
    if sandbox is not None:
        try:
            from harness.sandbox.tools import make_sandbox_tools

            tools.extend(make_sandbox_tools(sandbox, allow_bash=True))
        except Exception as exc:  # noqa: BLE001 —— 沙箱工具组装失败不影响其它工具
            logger.warning("沙箱工具组装失败，跳过沙箱工具组: %s", exc)

    # 2.内置工具组：上传清单 / 成品登记 / 技能审查 / 澄清 / 子代理派发
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
    # 3.看图工具只在当前模型支持视觉时注册（不支持则模型用不了，白占提示词预算）
    if supports_vision(app_config=app_config):
        tools.append(view_image_tool)

    # 4.web 工具组按开关注册；依赖缺失时降级跳过，不影响其它工具
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

    # 5.生图工具组按开关注册（火山方舟豆包 Seedream）
    if app_config.tools.ark_image.enabled:
        try:
            from harness.community.ark_image.tools import generate_image_tool

            tools.append(generate_image_tool)
        except Exception as exc:  # noqa: BLE001 —— 依赖缺失时可注入性失败
            logger.warning("generate_image 工具加载失败: %s", exc)

    # 6.记忆工具只在 memory.mode == "tool" 时注册
    #   （middleware 模式由中间件自动提取，不把记忆读写交给模型）
    if app_config.memory.mode == "tool":
        try:
            from harness.memory.integration import (
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

    # 7.最后统一过一遍白名单：被停用的工具既不注册、也不出现在系统提示词的工具清单里
    filtered = [tool for tool in tools if app_config.is_tool_enabled(tool.name)]
    dropped = [tool.name for tool in tools if tool.name not in {t.name for t in filtered}]
    if dropped:
        logger.info("按配置停用的工具: %s", ", ".join(sorted(dropped)))
    return filtered
