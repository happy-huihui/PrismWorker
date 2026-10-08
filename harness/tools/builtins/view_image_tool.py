from __future__ import annotations

import base64
import io
import logging
import shlex
from typing import Annotated

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.config.paths import VIRTUAL_PATH_PREFIX
from harness.tools.types import Runtime

"""看图工具

    职责：模型查看图片的入口——工具只登记元信息，像素由中间件按需注入
        - 路径白名单（workspace / uploads / outputs）+ 禁 .. 穿越
        - 登记不读像素：避免大图塞进每个 checkpoint
        - resolve_view_image_placeholder 在模型调用前把图转成 data URI（可 resize）

    对外暴露：
        - view_image_tool                  工具本体（写 state.viewed_images）
        - resolve_view_image_placeholder   沙箱图片 → data URI（供 before_model 中间件）
"""

logger = logging.getLogger(__name__)

_ALLOWED_IMAGE_VIRTUAL_ROOTS = (
    f"{VIRTUAL_PATH_PREFIX}/workspace",
    f"{VIRTUAL_PATH_PREFIX}/uploads",
    f"{VIRTUAL_PATH_PREFIX}/outputs",
)
_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT = "、".join(_ALLOWED_IMAGE_VIRTUAL_ROOTS)

_MAX_IMAGE_BYTES = 20 * 1024 * 1024

_EXTENSION_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _is_allowed_image_virtual_path(image_path: str) -> bool:
    """校验图片虚拟路径是否落在允许的根目录下。

    规则：
        - 必须是字符串、非空
        - 路径等于某个允许根目录，或在其子路径下（root/...）
        - 逐段检查，禁止 .. 穿越
    """
    if not isinstance(image_path, str) or not image_path.strip():
        return False
    # 归一为 POSIX 并去掉尾斜杠
    normalized = image_path.replace("\\", "/").strip().rstrip("/")
    # 逐段禁 .. 穿越
    if any(part == ".." for part in normalized.split("/")):
        return False
    # 必须命中白名单根目录本身或其子路径
    return any(
        normalized == root or normalized.startswith(f"{root}/")
        for root in _ALLOWED_IMAGE_VIRTUAL_ROOTS
    )


def _detect_image_mime(image_data: bytes) -> str | None:
    """按文件头 magic bytes 探测真实 MIME 类型。

    与扩展名推断相比，这是「内容级」校验：防止把任意文件伪装成图片。
    """
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(image_data) >= 12 and image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    if image_data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def _mime_from_extension(image_path: str) -> str | None:
    """按文件扩展名推断 MIME（登记元信息用，真实类型以 _detect_image_mime 为准）。"""
    filename = image_path.replace("\\", "/").split("/")[-1]
    if "." not in filename:
        return None
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    return _EXTENSION_TO_MIME.get(ext)


@tool("view_image", parse_docstring=True)
def view_image_tool(
    runtime: Runtime,
    file_path: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    resize: int | None = None,
) -> Command:
    """查看一张图片（供模型理解图片内容）。

    何时使用：
    - 任务涉及图片内容分析、截图检查、设计稿审阅等，需要把图片内容交给模型理解时。

    何时不要使用：
    - 非图片文件（用 present_files 或 read_file）
    - 一次查看多张图片（请分别调用，一次只看一张）

    说明：
    - 仅支持 jpg / jpeg / png / webp / gif 格式，路径必须在沙箱工作区内。
    - 本工具只登记图片元信息；图片正文会在模型读取时由系统按需注入。
    - 可传 resize（最长边像素数）让系统先缩放再注入，节省模型视觉 token。

    Args:
        file_path: 图片在沙箱内的虚拟路径（如 /mnt/user-data/workspace/截图.png）
        resize: 可选。图片最长边缩放到的像素数（如 1024）。不传则保留原尺寸。
    """
    # 1.路径白名单校验：不通过直接回错误，不登记
    if not _is_allowed_image_virtual_path(file_path):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"错误：图片路径必须在 {_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT} 下，"
                        f"且不允许路径穿越，收到: {file_path}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    # 2.扩展名不在支持列表 → 回错误
    mime_type = _mime_from_extension(file_path)
    if mime_type is None:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"错误：不支持的图片格式，仅支持 "
                        f"{', '.join(_EXTENSION_TO_MIME)}，收到: {file_path}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    # 3.resize 必须是正整数（最长边像素数）
    if resize is not None and (not isinstance(resize, int) or resize <= 0):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"错误：resize 必须是正整数（最长边像素数），收到: {resize!r}",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    # 4.只登记元信息（size 留 0 占位），像素留给中间件按需注入
    new_viewed_images = {
        file_path: {
            "mime_type": mime_type,
            "size": 0,
            "actual_path": file_path,
            "resize": resize,
        }
    }

    return Command(
        update={
            "viewed_images": new_viewed_images,
            "messages": [
                ToolMessage(
                    "已登记图片，模型读取时将自动注入图片内容", tool_call_id=tool_call_id
                )
            ],
        }
    )


def resolve_view_image_placeholder(
    sandbox: object,
    image_path: str,
    *,
    resize: int | None = None,
    max_bytes: int = _MAX_IMAGE_BYTES,
) -> str | None:
    """把沙箱里的图片转成 data URI，供 before_model 中间件注入消息。

    中间件在模型调用前遍历 state["viewed_images"]，对每张（尚未注入过的）图片
    调用本函数；成功返回 data URI 字符串，失败返回 None（中间件跳过并提示）。

    Args:
        sandbox:   AioSandbox 实例（提供 exec_command 读取容器内文件）
        image_path: 沙箱内图片的虚拟路径
        resize:    最长边像素数；提供时用 Pillow 等比缩放后再编码
        max_bytes: 图片大小上限，超出返回 None

    Returns:
        "data:image/xxx;base64,...." 字符串；失败返回 None。
    """
    from harness.sandbox.aio_sandbox import AioSandbox
    from harness.sandbox.exceptions import SandboxError

    # 1.本函数可能被别处直接调用，这里再校验一次路径
    if not _is_allowed_image_virtual_path(image_path):
        logger.warning("view_image: 拒绝读取非法路径 %s", image_path)
        return None

    try:
        # 2.先问容器文件大小，顺便确认文件存在
        size_cmd = f"stat -c %s {shlex.quote(image_path)}"
        size_out = (sandbox.exec_command(size_cmd) or "").strip()
        if not size_out.isdigit():
            logger.warning("view_image: 文件不存在或无法读取大小: %s", image_path)
            return None
        image_size = int(size_out)
        # 3.空文件或超上限直接放弃
        if image_size <= 0 or image_size > max_bytes:
            logger.warning(
                "view_image: 图片 %s 大小 %d 超出上限 %d",
                image_path,
                image_size,
                max_bytes,
            )
            return None

        b64_cmd = f"base64 -w0 {shlex.quote(image_path)}"
        b64_text = (sandbox.exec_command(b64_cmd) or "").strip()
        image_data = base64.b64decode(b64_text)
        # 4.核对字节数，防读取期间文件被改
        if len(image_data) != image_size:
            logger.warning("view_image: 图片内容在读取时发生变化: %s", image_path)
            return None
    except SandboxError as exc:
        logger.warning("view_image: 沙箱读取失败 %s: %s", image_path, exc)
        return None

    # 5.按 magic bytes 复核真实类型（扩展名只用于登记）
    detected_mime = _detect_image_mime(image_data)
    if detected_mime is None:
        logger.warning("view_image: 文件内容不是受支持的图片格式: %s", image_path)
        return None

    # 6.需要缩放时用 Pillow 等比缩到最长边，统一转 PNG
    if resize:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_data)) as img:
                if max(img.size) > resize:
                    img.thumbnail((resize, resize))
                buffer = io.BytesIO()
                img.convert("RGB").save(buffer, format="PNG")
                resized_bytes = buffer.getvalue()
                if len(resized_bytes) > max_bytes:
                    logger.warning("view_image: 缩放后仍超上限: %s", image_path)
                    return None
                image_data = resized_bytes
                detected_mime = "image/png"
        except Exception as exc:
            logger.warning("view_image: 图片缩放失败 %s: %s", image_path, exc)
            return None

    # 7.拼成 data URI 返回
    encoded = base64.b64encode(image_data).decode("ascii")
    return f"data:{detected_mime};base64,{encoded}"
