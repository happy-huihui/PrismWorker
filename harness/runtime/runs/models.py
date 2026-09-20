from __future__ import annotations

import time
from dataclasses import dataclass, field

"""run 数据模型（runs.models）

    职责：定义 run 编排用到的数据结构与状态常量、错误类型。
    内容：
        - 状态常量  pending / running / finished / cancelled / error（落 runs 表的字符串）
        - RunRecord  一次 run 的持久化记录（runs 表行映射，对外只读）
        - RunHandle  内存里的活动 run 句柄（运行期生命周期载体，含帧解析游标）
        - RunConflictError / RunNotFoundError  并发冲突 / 未找到
"""

# run 状态字符串（与 runs 表 status 列一致）
RUN_STATUS_PENDING = "pending"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_FINISHED = "finished"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_ERROR = "error"

# 终态集合（进入后不再变化）
TERMINAL_STATUSES: frozenset[str] = frozenset({
    RUN_STATUS_FINISHED,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_ERROR,
})

# runs 表列（更新/查询白名单，防注入）
RUN_COLUMNS = (
    "run_id",
    "thread_id",
    "user_id",
    "status",
    "model_name",
    "input_preview",
    "error",
    "artifacts",
    "message_count",
    "created_at",
    "started_at",
    "finished_at",
)


@dataclass
class RunRecord:
    """一次 run 的持久化记录（runs 表行的映射）。

    输出数据示例：
        RunRecord(run_id="9f3a…", thread_id="t-1", user_id="u",
                  status="finished", model_name="deepseek-v4-pro",
                  input_preview="帮我查天气", error=None,
                  artifacts=["outputs/report.md"], message_count=4,
                  created_at=1710000000.0, started_at=1710000000.1,
                  finished_at=1710000009.0)
    """

    run_id: str
    thread_id: str
    user_id: str
    status: str
    model_name: str = ""
    input_preview: str = ""
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)
    message_count: int = 0
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class RunHandle:
    """内存中的活动 run 句柄（运行期间的生命周期载体）。

    除公开字段外，还带一组「帧解析游标」私有字段（_last_* / _msg_round_*），
    由 worker 在解析 astream 帧时读写，用于做增量去重与按模型轮次判定
    思考/答复；仅进程内可见，不落库。
    """

    run_id: str
    thread_id: str
    user_id: str
    status: str = RUN_STATUS_PENDING
    model_name: str = ""
    thinking_enabled: bool = False
    input_preview: str = ""
    created_at: float = field(default_factory=time.time)
    task: object | None = None

    # prints / artifacts 增量游标，todos 指纹去重，最终答复文本累积
    _last_prints: int = 0
    _last_artifacts: int = 0
    _last_todos_fingerprint: str = ""
    _ai_chunks: list[str] = field(default_factory=list)

    # 模型轮次缓冲：按 checkpoint_ns 分轮，轮结束再定思考/答复流向
    _msg_round_ns: str | None = None
    _msg_round_text: list[str] = field(default_factory=list)
    _msg_round_has_tools: bool = False

    # 收尾幂等标记
    finalized: bool = False


class RunConflictError(Exception):
    """同线程已有未终结 run：并发冲突，应返回 409。"""

    def __init__(self, thread_id: str, run_id: str) -> None:
        """初始化；记录正在跑的 run_id 供 API 层提示。

        参数：
            thread_id: 冲突线程
            run_id: 正在进行的 run
        """
        super().__init__(f"线程 {thread_id} 已有进行中的 run: {run_id}")
        self.thread_id = thread_id
        self.run_id = run_id


class RunNotFoundError(Exception):
    """run 不存在。"""
