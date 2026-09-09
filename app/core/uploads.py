"""上传文件管理（uploads）——app/core 文件操作之二。

上传文件的磁盘落点：{base}/users/{uid}/threads/{tid}/user-data/uploads/
（沙箱侧虚拟路径 /mnt/user-data/uploads/）。上传/列出都返回带虚拟路径的
结构，前端与 agent 上下文（<current_uploads>）用的是同一套路径约定。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from harness.config.paths import get_paths


def _safe_filename(filename: str | None) -> str:
    """清洗上传文件名：取 basename、去空白、拒绝空名与穿越路径。"""
    name = (filename or "").strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("非法文件名")
    base = Path(name).name
    if not base:
        raise ValueError("非法文件名")
    return base


def save_upload(
    user_id: str,
    thread_id: str,
    *,
    filename: str | None,
    data: bytes,
    paths: Any | None = None,
) -> dict[str, Any]:
    """保存一个上传文件，返回 {filename, size, virtual_path}。

    uploads 目录不存在时自动创建（只建 uploads 目录，不动线程元数据与
    workspace/outputs）。paths 缺省用全局路径管理器（测试可注入）。
    """
    fname = _safe_filename(filename)
    paths = paths or get_paths()
    udir = paths.sandbox_uploads_dir(thread_id, user_id=user_id)
    udir.mkdir(parents=True, exist_ok=True)
    target = udir / fname
    target.write_bytes(data)
    size = len(data)
    return {
        "filename": fname,
        "size": size,
        "virtual_path": f"/mnt/user-data/uploads/{fname}",
    }


def list_uploads(
    user_id: str,
    thread_id: str,
    *,
    paths: Any | None = None,
) -> list[dict[str, Any]]:
    """列出线程已上传文件，按修改时间倒序（最新在前）。

    paths 缺省用全局路径管理器（测试可注入）。
    """
    paths = paths or get_paths()
    udir = paths.sandbox_uploads_dir(thread_id, user_id=user_id)
    if not udir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for p in udir.iterdir():
        if not p.is_file():
            continue
        items.append(
            {
                "filename": p.name,
                "size": p.stat().st_size,
                "virtual_path": f"/mnt/user-data/uploads/{p.name}",
                "modified_at": p.stat().st_mtime,
            }
        )
    items.sort(key=lambda i: i["modified_at"], reverse=True)
    for i in items:
        i.pop("modified_at", None)
    return items



async def _main() -> None:
    """命令行自检：python -m app.core.uploads <user_id> <thread_id>。"""
    import sys

    if len(sys.argv) < 3:
        print("用法: python -m app.core.uploads <user_id> <thread_id>")
        return
    items = list_uploads(sys.argv[1], sys.argv[2])
    print(f"共 {len(items)} 个文件:")
    for i in items:
        print(f"  [{i['virtual_path']}] {i['size']}B")


if __name__ == "__main__":
    asyncio.run(_main())