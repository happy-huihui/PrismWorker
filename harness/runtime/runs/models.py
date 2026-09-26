from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

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
    # 思考链事件回放流（仅历史回放路径填充；常规行查询不取此列，默认空）
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunHandle:
    """内存中的活动 run 句柄（运行期间的生命周期载体）。

    除公开字段外，还带一组「帧解析游标」私有字段（_last_* / _text_* / _msg_*），
    由 worker 在解析 astream 帧时读写：前者做增量去重，后两组做「逐 token 文本
    待发缓冲 + 按消息定性」；仅进程内可见，不落库。
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

    # prints / artifacts 增量游标，todos 指纹去重
    # run 的墙钟起点（任务清单收尾判断 todos_touched_at 是否属于本 run）
    _run_started_wall: float = 0.0
    _last_prints: int = 0
    _last_artifacts: int = 0
    _last_todos_fingerprint: str = ""

    # 文本待发缓冲：{(kind, message_id): 累积文本}，kind 取 reasoning / message；
    # 逐 token 到达但按时间片/字数节流下发，避免 SSE 帧数与前端重渲染爆炸
    _text_pending: dict[tuple[str, str], str] = field(default_factory=dict)
    # 上次冲刷文本缓冲的时刻（monotonic 秒），0 表示还没发过
    _text_last_flush: float = 0.0
    # 每条「消息 id + 轮次」已下发的正文累计（收尾算答复预览用；含后来被降级的那部分）
    # 键为 (message_id, round_ns)：DeepSeek 整个 run 复用同一个 message_id，
    # 只按 message_id 归并会把多轮正文错误地拼成一段，也无法区分是哪一轮被降级。
    _msg_text: dict[tuple[str, str], str] = field(default_factory=dict)
    # 已定性为「思考叙述」的 (message_id, round_ns)（该轮出现过工具调用 → 答复气泡里要剔除）
    _msg_retracted: set[tuple[str, str]] = field(default_factory=set)
    # 当前模型轮次的 checkpoint_ns（超步变化 = 上一轮结束，先冲刷缓冲）
    _msg_round_ns: str | None = None

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
