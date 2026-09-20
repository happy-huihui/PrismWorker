from __future__ import annotations

from harness.runtime.sandbox.runtime import (
    derive_sandbox_id,
    get_app_sandbox,
    get_runtime_thread_id,
    push_uploads_to_sandbox,
    release_app_sandbox,
    set_runtime_thread_id,
    stop_app_sandbox,
)

"""沙箱包（sandbox）

    职责：进程级真实沙箱的热池化懒启动 / 回源复用 / 上传推送 / 关闭回收。
"""

__all__ = [
    "set_runtime_thread_id",
    "get_runtime_thread_id",
    "derive_sandbox_id",
    "get_app_sandbox",
    "release_app_sandbox",
    "stop_app_sandbox",
    "push_uploads_to_sandbox",
]
