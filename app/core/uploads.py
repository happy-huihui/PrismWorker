from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from harness.config.paths import get_paths

"""上传文件管理

    职责：上传文件的落盘与列举（{user}/threads/{tid}/user-data/uploads/）
        - 文件名清洗：取 basename、拒空名与穿越路径
        - 返回值统一带沙箱虚拟路径，与 <current_uploads> 同一套约定

    对外暴露：
        - save_upload / list_uploads
"""

def _safe_filename(filename: str | None) -> str:
    """清洗上传文件名：取 basename、去空白、拒绝空名与穿越路径。"""
    # 1.统一分隔符后拒绝空名 / 绝对路径 / 含 .. 的路径
    name = (filename or "").strip().replace("\\", "/")
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError("非法文件名")
    # 2.只取 basename，杜绝借目录名越界
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
    # 1.先清洗文件名（非法直接抛 ValueError）
    fname = _safe_filename(filename)
    paths = paths or get_paths()
    udir = paths.sandbox_uploads_dir(thread_id, user_id=user_id)
    # 2.只建 uploads 目录，不动线程元数据与 workspace/outputs
    udir.mkdir(parents=True, exist_ok=True)
    target = udir / fname
    target.write_bytes(data)
    size = len(data)
    # 3.回带沙箱虚拟路径，与 <current_uploads> 同一套约定
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
    # 1.目录不存在 = 该线程没上传过
    if not udir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for p in udir.iterdir():
        # 2.只收普通文件（跳过子目录等）
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
    # 3.按修改时间倒序；排完再把内部排序键摘掉
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
