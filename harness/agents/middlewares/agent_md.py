from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware

from harness.prompt import render_text
from harness.runtime.agent_md import get_agent_md_store
from harness.runtime.user_context import resolve_runtime_user_id

if TYPE_CHECKING:
    from langchain_core.messages import SystemMessage

logger = logging.getLogger(__name__)

"""自定义指令中间件

    职责：每次模型调用前把用户的 agent.md 注入主代理系统提示词末尾
        - 用户级个人偏好层（对齐 Codex AGENTS.md），越靠后权重越高
        - 读失败 / 无内容 / 已注入都静默放行，不阻断对话

    对外暴露：
        - AgentMdMiddleware
"""

# 系统提示里已含该标记则视为已注入，避免重复包裹
_AGENT_MD_MARKER = "<agent_md>"


class AgentMdMiddleware(AgentMiddleware):
    """自定义指令中间件：把用户的 agent.md 追加到系统提示词末尾。"""

    async def awrap_model_call(
        self,
        request: Any,
        handler: Any,
    ) -> Any:
        """包装模型调用：注入用户自定义指令后放行（无指令/已注入/读失败均静默放行）。"""
        existing = request.system_message
        # 1.已注入过（含 agent_md 标记）就不重复注入，直接放行
        if existing is not None and _AGENT_MD_MARKER in existing.text:
            return await handler(request)
        # 2.解析当前用户
        try:
            user_id = resolve_runtime_user_id(request.runtime)
        except Exception:  # noqa: BLE001 —— 缺用户上下文时无指令可注入
            return await handler(request)
        # 3.读指令（mtime 缓存），空内容直接放行
        try:
            instruction = get_agent_md_store().load(user_id)
        except Exception as exc:  # noqa: BLE001 —— 指令异常不阻断对话
            logger.warning("自定义指令读取失败（user=%s）: %s", user_id, exc)
            return await handler(request)
        if not instruction.strip():
            return await handler(request)

        # 4.用集中托管的模板包成 <agent_md> 块（正文作值注入，不会被二次转义）
        block = render_text("agent_md/injection", {"user_instruction": instruction})
        # 5.追加到系统消息末尾（末尾指令权重更高，可对前文形成补充与覆盖）
        if existing is None:
            system_message: SystemMessage = SystemMessage(content=block)
        else:
            text = existing.text
            text = f"{text}\n\n{block}" if text else block
            from langchain_core.messages import SystemMessage as _SystemMessage

            system_message = _SystemMessage(content=text)
        return await handler(request.override(system_message=system_message))
