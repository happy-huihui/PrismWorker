from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, RemoveMessage

from harness.tools.builtins.view_image_tool import resolve_view_image_placeholder


"""view_image 中间件

    职责：把 state.viewed_images 里登记的图片在下次模型调用前读出注入，用完即删
        - 工具只登记路径元数据（不存 base64，避免撑爆 checkpoint）
        - 注入前从沙箱读取 → 缩放 → 生成 data URI
        - 沙箱不可用 / 图片不存在时跳过并记日志

    对外暴露：
        - ViewImageMiddleware
"""

logger = logging.getLogger(__name__)

_INJECTED_MARKER = "_view_image_injected"


class ViewImageMiddleware(AgentMiddleware):
    """图片查看中间件：模型调用前按需注入图片、调用后清理。"""

    async def abefore_model(
        self, state: Any, runtime: Any  # type: ignore[override]
    ) -> dict[str, Any] | None:
        """模型调用前：把登记过的图片转成 data URI 注入消息流。"""
        # 1.没有登记图片 → 直接返回
        viewed_images = (state or {}).get("viewed_images") or {}
        if not viewed_images:
            return None

        # 2.取沙箱实例（拿不到 → 跳过注入并记日志，不阻断对话）
        sandbox = self._resolve_sandbox(state)
        if sandbox is None:
            logger.warning("view_image 中间件取不到沙箱实例，跳过图片注入")
            return None

        # 3.逐张从磁盘读 → 缩放 → 生成 data URI
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
        # 4.一张都没成功 → 返回
        if not image_blocks:
            return None

        # 5.拼成带图片内容块的辅助消息（带注入标记，供调用后清理）
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
        # 1.找出带注入标记的辅助消息 id
        messages = (state or {}).get("messages") or []
        injected_ids = [
            message.id
            for message in messages
            if message is not None
            and message.additional_kwargs.get(_INJECTED_MARKER)
            and message.id
        ]
        # 2.没有辅助消息 → 返回
        if not injected_ids:
            return None

        # 3.删辅助消息（RemoveMessage）+ 空 dict 触发「清空」约定
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