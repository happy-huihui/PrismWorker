from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


"""上传文件列表中间件

    职责：把 state.uploaded_files 渲染成 <current_uploads> 块注入系统提示
        - 纯呈现层，不扫磁盘（扫描在 list_uploaded_files 工具内）
        - 无上传文件时保持静默

    对外暴露：
        - UploadsMiddleware
"""

_UPLOADS_MARKER = "current_uploads"


class UploadsMiddleware(AgentMiddleware):
    """上传文件列表中间件：把 state.uploaded_files 渲染进系统提示。"""

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],  # type: ignore[valid-type]
        handler: Any,
    ) -> ModelResponse:
        """包装模型调用：有已上传文件时注入 <current_uploads> 结构块。"""
        # 1.无上传文件 → 静默放行（不产生额外 token）
        state = request.state or {}
        uploaded_files = state.get("uploaded_files") or []
        if not uploaded_files:
            return await handler(request)

        # 2.逐条渲染成 <file name="..."> 行
        lines = []
        for entry in uploaded_files:
            name = entry.get("filename") if isinstance(entry, dict) else str(entry)
            if name:
                lines.append(f"<file name=\"{name}\">")
        # 3.没有有效文件行 → 放行
        if not lines:
            return await handler(request)

        # 4.已注入过（含标记）→ 不重复注入
        existing = request.system_message
        if existing is not None and _UPLOADS_MARKER in existing.text:
            return await handler(request)

        # 5.拼成 <current_uploads> 结构块并追加到系统消息末尾
        block = "\n".join(lines)
        structure = f"<{_UPLOADS_MARKER}>\n{block}\n</{_UPLOADS_MARKER}>"

        if existing is None:
            system_message = SystemMessage(content=structure)
        else:
            text = existing.text
            text = f"{text}\n\n{structure}" if text else structure
            system_message = SystemMessage(content=text)
        return await handler(request.override(system_message=system_message))