"""容器 → 宿主产物回传（transfer）——阶段15 交付闭环。

背景：真实沙箱容器未挂载宿主工作区（容器内存态 /mnt/user-data），
agent 在容器内产出的文件宿主不可见，present_files 登记的虚拟路径
没有物理文件可下载。本模块在 run 收尾把容器 outputs 目录的内容拉回
宿主线索 outputs 目录，让产物交付链路闭合。

实现：基于 exec_command 的文本通道做分块 base64 传输——
  1. find 列出容器内全部普通文件（相对路径 %POSIX 风格）
  2. 逐文件按块（块大小由 bash_output_max_chars 动态计算，保证单次
     命令的 base64 输出不超限）执行 `dd 分段 + base64 -w0` 读回
  3. 宿主解码写盘，用 stat -c%s 做完整字节数对账

限制与约定：
  - 仅回传普通文件（跳过目录 / 符号链接）
  - 单目录文件数上限 _MAX_FILES（默认 500），超出告警并以已列出的为准
  - 传输失败抛 SandboxTransferError；不影响 run 本身（收尾阶段容忍失败）
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

from harness.config.paths import VIRTUAL_PATH_PREFIX
from harness.sandbox.exceptions import SandboxError, SandboxPathError

logger = logging.getLogger(__name__)

_MAX_FILES = 500

_FIND_TEMPLATE = (
    "if [ -d '{src}' ]; then ( cd '{src}' && find . -type f | sed 's|^\\./||' | "
    f"head -n {_MAX_FILES} ); else echo SBX_OUTPUTS_MISSING; fi"
)


class SandboxTransferError(SandboxError):
    """容器 → 宿主文件回传失败（路径/内容/对账层面的错误）。"""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, details)


def pull_container_outputs(
    sandbox: object,
    host_dest_dir: str | Path,
    container_src_dir: str = f"{VIRTUAL_PATH_PREFIX}/outputs",
) -> int:
    """把容器内 container_src_dir 的全部普通文件回传到宿主 host_dest_dir。

    Args:
        sandbox: 真实 AioSandbox 实例（需具备 exec_command / _validate_path）
        host_dest_dir: 宿主目标目录（线程 outputs 目录，自动创建）
        container_src_dir: 容器源目录（必须在 /mnt/user-data 之下）

    Returns:
        成功回传的文件数量（无输出目录 / 空目录返回 0）

    Raises:
        SandboxTransferError: 任意一步失败（调用方按收尾容忍处理）
    """
    validate = getattr(sandbox, "_validate_path", None)
    exec_command = getattr(sandbox, "exec_command", None)
    if validate is None or exec_command is None:
        raise SandboxTransferError("回传目标不是有效沙箱实例（缺 exec_command / _validate_path）")
    try:
        safe_src = validate(container_src_dir)
    except SandboxPathError as exc:
        raise SandboxTransferError(f"容器源目录越界: {container_src_dir}") from exc

    dest = Path(host_dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    listing = exec_command(_FIND_TEMPLATE.format(src=safe_src))
    if "SBX_OUTPUTS_MISSING" in listing:
        logger.info("容器内无 outputs 目录（%s），跳过回传", safe_src)
        return 0
    rel_paths = _parse_listing(listing)
    if not rel_paths:
        logger.info("容器 outputs 为空目录，回传 0 个文件")
        return 0

    pulled = 0
    for rel_path in rel_paths:
        if ".." in rel_path.split("/"):
            raise SandboxTransferError(f"容器产物体含非法相对路径: {rel_path!r}")
        stat_out = exec_command(f"stat -c%s -- '{safe_src}/{rel_path}'; echo SBX_STAT_END")
        size_bytes = _parse_size(stat_out)
        if size_bytes is None:
            raise SandboxTransferError(
                f"无法确定容器文件大小: {rel_path}（输出={stat_out[:200]!r}）"
            )
        target = dest / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        written = _pull_single_file(sandbox, exec_command, safe_src, rel_path, target, size_bytes)
        if written:
            pulled += 1
    logger.info("容器 → 宿主回传完成：%d 个文件 → %s", pulled, dest)
    return pulled


def _parse_listing(listing: str) -> list[str]:
    """解析阶段一 find 输出：每行一条相对路径（整行即路径，不做分隔）。

    容错：exec_command 可能夹带非零退出码说明 / 截断提示等杂质行，跳过。
    """
    paths: list[str] = []
    for raw_line in listing.splitlines():
        line = raw_line.strip()
        if not line or line == "(no output)":
            continue
        if line.startswith("[") or line.startswith("...") or "bash:" in line[:6]:
            continue
        paths.append(line)
    return paths


def _parse_size(stat_out: str) -> int | None:
    """从 stat 输出中提取文件字节数（取第一个非负整数，直到结束标记）。"""
    end = stat_out.split("SBX_STAT_END")[0]
    for token in end.split():
        try:
            value = int(token)
            if value >= 0:
                return value
        except ValueError:
            continue
    return None


def _pull_single_file(
    sandbox: object,
    exec_command: object,
    container_dir: str,
    rel_path: str,
    target: Path,
    size_bytes: int,
) -> int:
    """回传单个文件：分块 dd + base64，写盘并对账字节数。

    Returns: 1 成功 / 0 空文件（0 字节也写入但计数为成功）。
    """
    output_limit = getattr(sandbox, "bash_output_max_chars", 30000)
    block_raw = int(output_limit * 3 / 4 * 0.95)
    block_raw = max(1024, min(block_raw, 1 << 20))
    if block_raw < 4096:
        block_raw = 4096

    container_path = f"{container_dir}/{rel_path}"
    if size_bytes == 0:
        target.write_bytes(b"")
        logging.getLogger(__name__).debug("回传空文件: %s", rel_path)
        return 1

    written = 0
    offset = 0
    while offset < size_bytes:
        chunk = _read_base64_chunk(exec_command, container_path, offset, block_raw)
        raw = base64.b64decode(chunk, validate=True)
        if not raw:
            break
        with target.open("ab") as fh:
            fh.write(raw)
        written += len(raw)
        offset += len(raw)
        if len(raw) < block_raw and offset < size_bytes:
            raise SandboxTransferError(
                f"分块读取提前结束: {rel_path} 期望 {size_bytes} 字节，"
                f"已读 {offset} 字节（容器输出可能被截断）"
            )
        if offset >= size_bytes:
            break

    if written != size_bytes:
        raise SandboxTransferError(
            f"回传字节数不符: {rel_path} 期望 {size_bytes}，实际 {written}"
        )
    logging.getLogger(__name__).debug("回传文件完成: %s (%d bytes)", rel_path, written)
    return 1


def _read_base64_chunk(exec_command: object, container_path: str, offset: int, raw_len: int) -> str:
    """用 dd 读取容器文件的一个分片并 base64 编码返回。

    命令：dd if='{path}' bs={raw} skip={offset//raw} count=1 2>/dev/null | base64 -w0; echo SBX_EOF
    输出仅在末尾包含 SBX_EOF 时视为完整；否则抛错（防截断）。
    """
    bs = raw_len
    skip = offset // bs
    command = (
        f"dd if='{container_path}' bs={bs} skip={skip} count=1 2>/dev/null | base64 -w0; echo SBX_EOF"
    )
    out = exec_command(command)
    marker = "SBX_EOF"
    if marker not in out:
        raise SandboxTransferError(
            f"容器分块输出不完整（缺结束标记）: {container_path} @ {offset} (输出={out[:200]!r})"
        )
    payload = out.split(marker)[0]
    return re.sub(r"[^A-Za-z0-9+/=]", "", payload)