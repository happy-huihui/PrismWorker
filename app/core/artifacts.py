from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from harness.config.paths import get_paths

"""产物下载解析

    职责：把产物虚拟路径解析为宿主机真实文件路径，供 API 以文件流返回
        - 仅允许 outputs 目录下的文件
        - 显式拦 .. 穿越，再复用 harness 的双重防穿越校验

    对外暴露：
        - resolve_artifact_path
"""

def resolve_artifact_path(
    user_id: str,
    thread_id: str,
    relative_path: str,
    *,
    paths: Any | None = None,
) -> Path | None:
    """解析产物虚拟路径 → 宿主机真实路径。

    仅允许访问 outputs 目录下的文件（与交付工具约束一致）。
    目录越界/路径穿越 → 抛 ValueError；文件不存在 → 返回 None。
    paths 缺省用全局路径管理器（测试可注入）。
    """
    # 1.去空白与首斜杠（调用方可能带前导 /）
    rel = (relative_path or "").strip().lstrip("/")
    if not rel:
        raise ValueError("产物路径不能为空")
    # 2.显式拦 .. 穿越（起头 / 中间 / 结尾三种写法都覆盖）
    if (
        rel == ".."
        or rel.startswith("../")
        or "/../" in "/" + rel
        or rel.endswith("/..")
    ):
        raise ValueError("产物路径越界：不允许 .. 跳出 outputs 目录")
    # 3.拼成沙箱虚拟路径，交给 harness 做双重防穿越校验
    vpath = f"/mnt/user-data/outputs/{rel}"
    paths = paths or get_paths()
    actual = paths.resolve_virtual_path(thread_id, vpath, user_id=user_id)
    # 4.文件不存在返回 None（调用方映射 404）
    if not actual.is_file():
        return None
    return actual



async def _main() -> None:
    """命令行自检：python -m app.core.artifacts <user_id> <thread_id> <相对路径>。"""
    import sys

    if len(sys.argv) < 4:
        print("用法: python -m app.core.artifacts <user_id> <thread_id> <相对路径>")
        return
    try:
        p = resolve_artifact_path(sys.argv[1], sys.argv[2], sys.argv[3])
    except ValueError as exc:
        print(f"拒绝访问: {exc}")
        return
    print(f"存在: {p}" if p else "文件不存在")


if __name__ == "__main__":
    asyncio.run(_main())
