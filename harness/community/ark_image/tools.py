from __future__ import annotations

import base64
import logging
import mimetypes
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

from harness.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from harness.tools.types import Runtime
from harness.runtime.user_context import resolve_runtime_user_id

"""generate_image 工具

    职责：把「生成一张图」端到端做完——调接口 → 立即落盘 outputs → 登记产物
        - 图片 URL 仅 24h 有效，必须马上下载到宿主 outputs
        - 落盘走宿主机（生图请求在宿主发起、字节已在宿主，不绕沙箱）
        - 自带登记，模型无需再对这些图片调 present_files

    对外暴露：
        - generate_image_tool
        - OUTPUTS_VIRTUAL_PREFIX
"""

logger = logging.getLogger(__name__)

"""产物登记用的虚拟前缀（与 present_file.py 保持一致）。"""
OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"

"""图片扩展名推断：官方 output_format 默认 jpeg，URL 上也常带扩展名。"""
_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}

"""单张图片落盘后的最大文件名长度（防超长文件名打爆文件系统）。"""
_MAX_FILENAME_STEM = 60


def _sanitize_stem(text: str) -> str:
    """把用户/模型给的描述文本压成安全的文件名主干。

    参数：
        text: 原始描述（可能含中文、空格、斜杠、控制字符）

    返回：
        仅含字母/数字/中文/短横线/下划线的短主干；全被过滤掉时返回空串
    """
    if not text:
        return ""
    # 逐字符白名单过滤：中日韩文字、字母数字、- _ 保留，其余换短横线
    kept: list[str] = []
    for ch in text.strip():
        if ch.isalnum() or ch in "-_":
            kept.append(ch)
        elif ch.isspace() or ch in "/\\:*?\"<>|":
            kept.append("-")
    stem = "".join(kept).strip("-")
    # 折叠连续短横线
    while "--" in stem:
        stem = stem.replace("--", "-")
    return stem[:_MAX_FILENAME_STEM].strip("-")


def _guess_extension(url: str, content_type: str | None, index: int) -> str:
    """推断图片扩展名（按 URL 后缀 → Content-Type → 默认 jpg 逐级回退）。"""
    # 1.优先看 URL 路径后缀（去掉 query）
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
        return ".jpg" if suffix == ".jpeg" else suffix
    # 2.退到 Content-Type
    if content_type:
        main = content_type.split(";")[0].strip().lower()
        if main in _EXT_BY_MIME:
            return _EXT_BY_MIME[main]
    # 多图时加序号，避免同名覆盖
    # 3.再兜底 jpg；多图加序号避免同名覆盖
    return ".jpg" if index == 0 else f"-{index}.jpg"


def _thread_id_from_runtime(runtime: Runtime) -> str | None:
    """从运行时上下文解析 thread_id（与 present_file 同款三阶梯回退）。"""
    # 1.优先 runtime.context
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return str(thread_id)
    # 2.退到 runtime.config.configurable
    runtime_config = getattr(runtime, "config", None) or {}
    if isinstance(runtime_config, dict):
        found = runtime_config.get("configurable", {}).get("thread_id")
        if found:
            return str(found)
    # 3.再退到图全局 config（无运行时上下文时抛 RuntimeError）
    try:
        found = get_config().get("configurable", {}).get("thread_id")
        if found:
            return str(found)
    except RuntimeError:
        pass
    return None


def _save_bytes(thread_id: str, user_id: str, filename: str, data: bytes) -> Path:
    """把图片字节写到本线程 outputs 目录，返回宿主机真实路径。

    参数：
        thread_id: 线程 id
        user_id: 用户 id
        filename: 目标文件名（已含扩展名）
        data: 图片字节

    返回：
        宿主机真实文件路径
    """
    # 1.定位本线程 outputs 目录并确保存在
    paths = get_paths()
    outputs_dir = paths.sandbox_outputs_dir(thread_id, user_id=user_id)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    # 2.双重防穿越：即使文件名被污染也不允许跳出 outputs
    target = (outputs_dir / filename).resolve()
    # 双重防穿越：即使 filename 被污染也不允许跳出 outputs
    try:
        target.relative_to(outputs_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"文件名非法（越界 outputs 目录）：{filename}") from exc
    target.write_bytes(data)
    return target


def _virtual_of(thread_id: str, user_id: str, real_path: Path) -> str:
    """把宿主机真实路径转成前端统一识别的产物虚拟路径。"""
    # 与 present_files 同一套虚拟路径约定，前端才能识别
    outputs_dir = get_paths().sandbox_outputs_dir(thread_id, user_id=user_id).resolve()
    relative = real_path.resolve().relative_to(outputs_dir)
    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative.as_posix()}"


def _tool_message(content: str, tool_call_id: str) -> Command:
    """构造只回消息、不动状态的工具返回（失败路径用）。"""
    return Command(update={"messages": [ToolMessage(content, tool_call_id=tool_call_id)]})


def _artifact_message(content: str, tool_call_id: str, artifacts: list[str]) -> Command:
    """构造「回消息 + 登记产物」的工具返回。

    为什么必须显式写 artifacts 通道：
        ThreadState.artifacts 是带 reducer（merge_artifacts）的状态通道，
        只有节点返回的 Command.update 里出现 "artifacts" 键才会被合并进去。
        前端产物栏读的就是这个通道——只回 ToolMessage 的话，图片虽然落盘了，
        但用户界面上不会出现任何产物卡片，等于「生成了却交付不到」。
    """
    # 显式写 artifacts 键，reducer 才会把产物合并进 ThreadState
    return Command(
        update={
            "messages": [ToolMessage(content, tool_call_id=tool_call_id)],
            "artifacts": list(artifacts),
        }
    )


def _download(url: str, timeout: float) -> tuple[bytes, str | None]:
    """下载图片字节，返回 (字节, Content-Type)。"""
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        # 跟随重定向（图片 CDN 常 302）；非 2xx 抛错由调用方按单张失败处理
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type")


def _generate_and_register(
    *,
    prompt: str,
    size: str | None,
    model: str | None,
    filename: str | None,
    image: str | list[str] | None,
    runtime: Runtime,
    tool_call_id: str,
) -> Command:
    """生图主流程：调接口 → 落盘 → 登记产物 → 回执。"""
    from harness.community.ark_image.client import ArkImageError, build_client

    # 1. 定位线程与用户（落盘与登记都要用）
    # 1.定位线程与用户（落盘与登记都要用）
    thread_id = _thread_id_from_runtime(runtime)
    if not thread_id:
        return _tool_message("生图失败：无法解析当前线程 id", tool_call_id)
    user_id = resolve_runtime_user_id(runtime)

    # 2. 调 Ark 生图
    # 2.构造客户端；失败必须留痕（否则排查只能解剖 checkpoint）
    try:
        client = build_client()
    except ArkImageError as exc:
        # 失败必须留痕：此前失败路径只回 ToolMessage 不写日志，排查只能解剖 checkpoint
        logger.warning("生图失败（构造客户端）：%s", exc)
        return _tool_message(f"生图失败：{exc}", tool_call_id)

    # 3.调接口（异常信息已含状态码 / 错误码 / 处置建议，不要再手工拼）
    try:
        result = client.generate(prompt, size=size, model=model, image=image)
    except ArkImageError as exc:
        # ArkImageError.__str__ 已含 状态码 / 错误码 / 处置建议 / 官方信息，
        # 这里不要再手工拼 exc.detail，否则会重复。
        logger.warning("生图失败（接口调用）：%s", exc)
        return _tool_message(f"生图失败：{exc}", tool_call_id)

    # 3. 逐张落盘（url 形式先下载；b64_json 形式直接解码）
    timeout = float(client.config.timeout_seconds)
    stem = _sanitize_stem(filename or "") or f"{client.config.filename_prefix}-{_timestamp()}"
    saved: list[str] = []
    errors: list[str] = []

    # 4.逐张落盘：url 形式先下载、b64 形式直接解码；单张失败不阻断其余
    for index, url in enumerate(result.urls):
        try:
            data, content_type = _download(url, timeout)
        except Exception as exc:  # noqa: BLE001 —— 单张失败不阻断其余图片
            errors.append(f"第 {index + 1} 张下载失败：{exc}")
            continue
        ext = _guess_extension(url, content_type, index)
        saved.append(_persist(stem, index, ext, data, thread_id, user_id))

    for index, encoded in enumerate(result.b64_list):
        try:
            data = base64.b64decode(encoded)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"第 {index + 1} 张 base64 解码失败：{exc}")
            continue
        saved.append(_persist(stem, index + len(result.urls), ".png", data, thread_id, user_id))

    # 5.一张都没成功才算整体失败
    if not saved:
        joined = "；".join(errors) or "未返回图片数据"
        logger.warning("生图失败（产物落盘）：%s", joined)
        return _tool_message(f"生图失败：{joined}", tool_call_id)

    # 4. 回执：成功登记了几张、虚拟路径是什么、以及失败提示
    # 6.回执：成功清单 + 失败提示 + 「无需再调 present_files」说明
    header = f"已生成 {len(saved)} 张图片并登记为交付产物："
    lines = [f"- {path}" for path in saved]
    tail = ""
    if errors:
        tail = "\n\n部分图片处理失败：\n" + "\n".join(f"- {e}" for e in errors)
    hint = "\n\n（这些图片已自动登记为产物，无需再调用 present_files；如需直接展示给用户看，可用 view_image。）"
    return _artifact_message(
        f"{header}\n" + "\n".join(lines) + tail + hint,
        tool_call_id,
        saved,
    )


def _timestamp() -> str:
    """当前时间戳（文件名用，本地时区，形如 20260923-153000）。"""
    return datetime.fromtimestamp(time.time()).strftime("%Y%m%d-%H%M%S")


def _persist(
    stem: str,
    index: int,
    ext: str,
    data: bytes,
    thread_id: str,
    user_id: str,
) -> str:
    """落盘一张图片并返回其虚拟路径（多图自动加序号）。"""
    # 首张不加序号，多图从 -2 开始编号
    name = f"{stem}{ext}" if index == 0 else f"{stem}-{index + 1}{ext}"
    real = _save_bytes(thread_id, user_id, name, data)
    return _virtual_of(thread_id, user_id, real)


@tool("generate_image", parse_docstring=True)
def generate_image_tool(
    prompt: str,
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
    size: str | None = None,
    filename: str | None = None,
    image: str | list[str] | None = None,
) -> Command:
    """根据文字描述生成图片，也可给参考图做图生图 / 多图融合（火山方舟豆包生图）。

    什么时候用：
    - 用户要求画图、生成插画 / 海报 / 概念图 / 配图时。
    - 需要为网页、文档、报告配一张示意图片时。
    - 用户给了参考图，要「照着这张图改 / 把几张图融合成一张」时（用 image 传图的 URL）。

    什么时候不要用：
    - 用户只是想「看」某张已有图片（用 view_image）。
    - 需要的是可编辑的矢量图 / 图表（用代码画，如 matplotlib / SVG）。

    说明：生成的图片会自动保存并登记为交付产物，直接出现在用户界面的
    产物栏中，无需你再调用 present_files。

    Args:
        prompt: 图片内容描述。写得越具体效果越好：主体、风格、构图、色调、
            光线、画幅比例等。支持中文。
        size: 图像尺寸，可选 "2K"（默认）/ "4K" / 指定宽高如 "2048x2048"。
        filename: 输出文件名主干（不含扩展名），省略时自动按时间戳命名。
        image: 参考图（可选）：图片 URL 或 URL 列表，用于图生图 / 多图融合；
            必须是公网可访问的 URL，本地虚拟路径（/mnt/user-data/...）无效。
    """
    # 工具层只做转发，主流程在 _generate_and_register
    return _generate_and_register(
        prompt=prompt,
        size=size,
        model=None,
        filename=filename,
        image=image,
        runtime=runtime,
        tool_call_id=tool_call_id,
    )
