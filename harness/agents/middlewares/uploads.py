from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


"""
    上传文件列表中间件（uploads）——维护「本次运行已上传文件」清单。

    本 harness 的上传文件列表由网关写入 state.uploaded_files（list[dict]）。
    该中间件做两件事：
      1. 每次模型调用前，把当前已上传文件渲染成 <current_uploads> 结构块
         注入系统提示，让模型和 list_uploaded_files 工具知道哪些文件属于
         本次运行（list_uploaded_files 会排除这些，避免重复展示）；
      2. 不直接扫描磁盘（扫描职责在 list_uploaded_files 工具内），
         只负责「状态 → 提示词」的呈现层同步。
    无上传文件时保持静默，不产生任何额外 token。
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
        state = request.state or {}
        uploaded_files = state.get("uploaded_files") or []
        if not uploaded_files:
            return await handler(request)

        lines = []
        for entry in uploaded_files:
            name = entry.get("filename") if isinstance(entry, dict) else str(entry)
            if name:
                lines.append(f"<file name=\"{name}\">")
        if not lines:
            return await handler(request)

        existing = request.system_message
        if existing is not None and _UPLOADS_MARKER in existing.text:
            return await handler(request)

        block = "\n".join(lines)
        structure = f"<{_UPLOADS_MARKER}>\n{block}\n</{_UPLOADS_MARKER}>"

        if existing is None:
            system_message = SystemMessage(content=structure)
        else:
            text = existing.text
            text = f"{text}\n\n{structure}" if text else structure
            system_message = SystemMessage(content=text)
        return await handler(request.override(system_message=system_message))