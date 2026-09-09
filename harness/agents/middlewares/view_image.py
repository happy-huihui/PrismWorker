from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage

from harness.tools.builtins.view_image_tool import resolve_view_image_placeholder


"""
    view_image 中间件——落实「工具只登记、中间件按需注入」的图片查看约定。

    场景：模型调用 view_image 工具登记想看某张图片（state.viewed_images 只存
    路径元数据，不存 base64，避免撑爆 checkpoint）。真正把图片字节交给模型，
    是在「下一次模型调用」之前由本中间件完成：

      abefore_model（模型调用前）：
        遍历 state.viewed_images，对每张未注入的图片调用
        resolve_view_image_placeholder() 从沙箱读取 → 缩放 → 生成 data URI，
        拼成一条带图片内容块的辅助消息追加进 messages；

      aafter_model（模型调用后）：
        把上一步注入的辅助消息用 RemoveMessage 删掉（打扫战场），
        并写入空 dict 触发 merge_viewed_images 的「清空」约定。

    这样模型每次「看」图片都从最新磁盘状态读，会话历史里不留字节副本。
    沙箱不可用 / 图片不存在时跳过注入并记录日志（不阻断对话）。
"""

logger = logging.getLogger(__name__)

_INJECTED_MARKER = "_view_image_injected"


class ViewImageMiddleware(AgentMiddleware):
    """图片查看中间件：模型调用前按需注入图片、调用后清理。"""

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：把登记过的图片转成 data URI 注入消息流。"""
        viewed_images = (state or {}).get("viewed_images") or {}
        if not viewed_images:
            return None

        sandbox = self._resolve_sandbox(state)
        if sandbox is None:
            logger.warning("view_image 中间件取不到沙箱实例，跳过图片注入")
            return None

        image_blocks: list[dict[str, Any]] = []
        for image_path, meta in viewed_images.items():
            data_uri = resolve_view_image_placeholder(
                sandbox,
                str(image_path),
                resize=meta.get("resize") if isinstance(meta, dict) else None,
            )
            if data_uri is None:
                logger.warning("view_image 注入失败: %s", image_path)
                continue
            image_blocks.append(
                {"type": "image_url", "image_url": {"url": data_uri}}
            )
        if not image_blocks:
            return None

        note = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": "以下是用户请求查看的图片内容，请按需分析：",
                },
                *image_blocks,
            ],
            additional_kwargs={_INJECTED_MARKER: True},
        )
        note.id = str(uuid.uuid4())

        return {"messages": [note]}

    async def aafter_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用后：删除注入的辅助消息并清空 viewed_images。"""
        messages = (state or {}).get("messages") or []
        injected_ids = [
            message.id
            for message in messages
            if message is not None
            and message.additional_kwargs.get(_INJECTED_MARKER)
            and message.id
        ]
        if not injected_ids:
            return None

        return {
            "messages": [RemoveMessage(id=message_id) for message_id in injected_ids],
            "viewed_images": {},
        }

    @staticmethod
    def _resolve_sandbox(state: Any) -> Any | None:
        """按 state.sandbox.sandbox_id 从进程级管理器取沙箱实例。"""
        sandbox_state = state.get("sandbox") if isinstance(state, dict) else None
        if not isinstance(sandbox_state, dict):
            return None
        sandbox_id = sandbox_state.get("sandbox_id")
        if not sandbox_id:
            return None
        try:
            from harness.sandbox.lifecycle import get_sandbox_manager

            return get_sandbox_manager().get(str(sandbox_id))
        except Exception as exc:  # noqa: BLE001 —— 沙箱不可用不应阻断对话
            logger.warning("view_image 获取沙箱 %s 失败: %s", sandbox_id, exc)
            return None