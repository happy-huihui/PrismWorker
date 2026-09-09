"""子代理执行器：把 task 工具派发的任务变成真正的子代理运行。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError

from harness.agents.thread_state import ThreadState
from harness.models.factory import create_chat_model
from harness.subagents.config import SubagentConfig, resolve_subagent_model_name

logger = logging.getLogger(__name__)


@dataclass
class SubagentResult:
    """一次子代理执行的最终结果（task 工具壳按此结构取 status/result/error）。

    字段说明：
        status:       结束状态，取值见 status_contract（completed / failed /
                      timed_out 等终止态）
        result:       成功时的最终结果正文
        error:        失败 / 超时时的错误描述
        stop_reason:  被护栏提前收尾的原因附加标记（turn_capped / timed_out）
        task_id:      本次执行的任务 id（观察性标识）
        started_at / completed_at: 执行起止时间
    """

    status: str
    result: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None


class SubagentExecutor:
    """子代理执行器。

    职责：解析模型名 → 按配置组装「完整工具集减黑名单」→ 在一个全新
    上下文里用 create_agent 跑「模型-工具」循环 → 汇总最终结果给父代理。

    与 task 工具壳的构造契约：
        SubagentExecutor(config=, parent_model=, sandbox_state=,
                         thread_data=, thread_id=, user_id=)
        result = await executor.execute(prompt)
    """

    def __init__(
        self,
        config: SubagentConfig,
        *,
        parent_model: str | None = None,
        sandbox_state: dict | None = None,
        thread_data: dict | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
    ):
        self.config = config
        self.parent_model = parent_model
        self.thread_data = dict(thread_data or {})
        self.thread_id = thread_id
        self.user_id = user_id

        self.sandbox_id: str | None = None
        if isinstance(sandbox_state, dict):
            self.sandbox_id = sandbox_state.get("sandbox_id")

        self._model: Any = None
        self._tools: list[BaseTool] | None = None


    async def execute(self, prompt: str) -> SubagentResult:
        """在新上下文里把子代理任务完整跑完。

        Args:
            prompt: 父代理写给子代理的任务指令。

        Returns:
            SubagentResult（status 一定是终止态：completed / failed / timed_out）。
        """
        started_at = datetime.now()
        result = SubagentResult(status="completed", started_at=started_at)

        model = self._get_model()
        tools = self._get_tools()
        logger.info(
            "子代理[%s] 任务[%s] 开始执行：模型=%s 工具=%d 个 轮次上限=%d",
            self.config.name,
            result.task_id,
            getattr(model, "model_name", "?"),
            len(tools),
            self.config.max_turns,
        )

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=self.config.system_prompt,
            state_schema=ThreadState,
        )

        initial_state: dict[str, Any] = {
            "messages": [HumanMessage(content=prompt)],
            "thread_data": self.thread_data,
            "viewed_images": {},
        }
        if self.sandbox_id:
            initial_state["sandbox"] = {"sandbox_id": self.sandbox_id}

        final_state: dict[str, Any] | None = None

        async def _run() -> None:
            nonlocal final_state
            async for chunk in agent.astream(
                initial_state,
                config={"recursion_limit": self.config.max_turns},
                stream_mode="values",
            ):
                final_state = chunk

        try:
            await asyncio.wait_for(_run(), timeout=self.config.timeout_seconds)
        except TimeoutError:
            result.completed_at = datetime.now()
            result.status = "timed_out"
            result.stop_reason = "timed_out"
            result.error = f"子代理执行超过 {self.config.timeout_seconds} 秒，已终止"
            return result
        except GraphRecursionError:
            partial = _extract_final_message(final_state)
            result.completed_at = datetime.now()
            result.stop_reason = "turn_capped"
            if partial:
                result.status = "completed"
                result.result = partial
            else:
                result.status = "failed"
                result.error = f"达到轮次上限 {self.config.max_turns} 且没有产出有效结果"
            return result
        except Exception as exc:  # noqa: BLE001 —— 任何异常都要转成可读结果交给父代理
            logger.exception("子代理[%s] 任务[%s] 执行失败", self.config.name, result.task_id)
            result.completed_at = datetime.now()
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            return result

        result.completed_at = datetime.now()
        body = _extract_final_message(final_state)
        if body:
            result.status = "completed"
            result.result = body
        else:
            result.status = "failed"
            result.error = "子代理执行结束但没有产出有效结果"
        return result


    def _get_model(self) -> Any:
        """解析模型名并创建聊天模型（懒加载）。"""
        if self._model is None:
            model_name = resolve_subagent_model_name(self.config, self.parent_model)
            self._model = create_chat_model(name=model_name, thinking_enabled=False)
            logger.debug("子代理[%s] 解析到模型: %s", self.config.name, model_name)
        return self._model

    def _get_tools(self) -> list[BaseTool]:
        """组装子代理的工具集：完整工具集（沙箱 + 内置 + web）再按白/黑名单过滤。

        - 沙箱工具：连到父代理同一个容器；拿不到沙箱实例就跳过（不报错）
        - 内置工具：除 task 外全部注册（task 在 disallowed_tools 黑名单里被过滤）
        - web 工具：web_search + web_fetch
        """
        if self._tools is not None:
            return self._tools

        sandbox_tools: list[BaseTool] = []
        sandbox = self._get_sandbox()
        if sandbox is not None:
            from harness.sandbox.tools import make_sandbox_tools

            sandbox_tools = make_sandbox_tools(sandbox, allow_bash=True)

        from harness.tools.builtins import (
            ask_clarification_tool,
            list_uploaded_files,
            present_file_tool,
            review_skill_package,
            view_image_tool,
        )
        builtin_tools: list[BaseTool] = [
            ask_clarification_tool,
            list_uploaded_files,
            present_file_tool,
            review_skill_package,
            view_image_tool,
        ]

        from harness.community.tavily_search.tools import web_search_tool
        from harness.community.web_fetch.tools import web_fetch_tool

        web_tools: list[BaseTool] = [web_search_tool, web_fetch_tool]

        all_tools = [*sandbox_tools, *builtin_tools, *web_tools]
        self._tools = _filter_tools(
            all_tools,
            allowed=self.config.tools,
            disallowed=self.config.disallowed_tools,
        )
        logger.debug(
            "子代理[%s] 工具集组装：全量 %d → 有效 %d（沙箱=%s）",
            self.config.name,
            len(all_tools),
            len(self._tools),
            "已连接" if sandbox is not None else "未连接",
        )
        return self._tools

    def _get_sandbox(self) -> Any | None:
        """按 sandbox_id 从进程级沙箱管理器取回容器实例；取不到返回 None。"""
        if not self.sandbox_id:
            return None
        try:
            from harness.sandbox.lifecycle import get_sandbox_manager

            return get_sandbox_manager().get(self.sandbox_id)
        except Exception as exc:  # noqa: BLE001 —— 沙箱不可用不影响子代理启动
            logger.warning("子代理[%s] 获取沙箱 %s 失败: %s", self.config.name, self.sandbox_id, exc)
            return None


def _filter_tools(
    all_tools: list[BaseTool],
    allowed: list[str] | None,
    disallowed: list[str] | None,
) -> list[BaseTool]:
    """按子代理配置过滤工具：先过白名单，再过黑名单。

    Args:
        all_tools: 全量工具列表。
        allowed: 白名单；None 表示全部保留。
        disallowed: 黑名单；命中的工具一律剔除。

    Returns:
        过滤后的工具列表。
    """
    if allowed is not None:
        allowed_set = set(allowed)
        all_tools = [t for t in all_tools if t.name in allowed_set]
    if disallowed:
        denied = set(disallowed)
        all_tools = [t for t in all_tools if t.name not in denied]
    return all_tools


def _extract_final_message(state: dict[str, Any] | None) -> str:
    """从图终态里提取最后一条 AI 消息的正文（找不到返回空串）。"""
    if not state:
        return ""
    messages = state.get("messages") or []
    for message in reversed(messages):
        if getattr(message, "type", "") != "ai":
            continue
        content = message.content
        if isinstance(content, str):
            if content.strip():
                return content.strip()
        elif isinstance(content, list):
            parts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            text = "".join(parts).strip()
            if text:
                return text
    return ""