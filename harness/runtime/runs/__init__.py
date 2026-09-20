from __future__ import annotations

from harness.runtime.runs.manager import RunManager
from harness.runtime.runs.models import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_ERROR,
    RUN_STATUS_FINISHED,
    RUN_STATUS_PENDING,
    RUN_STATUS_RUNNING,
    RunConflictError,
    RunHandle,
    RunNotFoundError,
    RunRecord,
)
from harness.runtime.runs.store import RunStore, row_to_record
from harness.runtime.runs.worker import run_worker

"""run 编排包（runs）

    职责：一次 agent run 的完整编排——创建/取消/查询/等待 + 后台执行 + 落库。
    结构：models 数据模型、store runs 表读写、worker 后台驱动与帧解析、
         manager 对外编排入口。
"""

__all__ = [
    "RunManager",
    "RunStore",
    "row_to_record",
    "run_worker",
    "RunRecord",
    "RunHandle",
    "RunConflictError",
    "RunNotFoundError",
    "RUN_STATUS_PENDING",
    "RUN_STATUS_RUNNING",
    "RUN_STATUS_FINISHED",
    "RUN_STATUS_CANCELLED",
    "RUN_STATUS_ERROR",
]
