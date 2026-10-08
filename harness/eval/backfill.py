from __future__ import annotations

import logging
from typing import Any

"""线上采样回灌（backfill）——把线上真实 run 转成评测样本上传 LangSmith。

    职责：从 runs 表采样线上 run（失败优先 + 成功按比例），从 checkpoint 读出
        「当时的用户输入 + agent 回答」→ 构成 {question, reference} 评测样本，
        上传到独立数据集（prismworker-online，与手写 golden 分开）。
        reference = 旧版本输出，语义是回归基准：评新版本是否比当时退步。

    对外暴露：
        - backfill   采样 → 构造样本 → 上传，返回写入条数
"""

logger = logging.getLogger(__name__)


async def sample_runs(*, limit: int, error_ratio: float) -> list[dict[str, Any]]:
    """从 runs 表采样线上 run：失败（error）优先，剩余名额给成功样本。

    参数：
        limit: 采样总条数
        error_ratio: 失败样本占比（0~1，钳制）

    返回：
        [{run_id, thread_id, user_id, input_preview, status}]
    """
    from harness.runtime.runs.store import RunStore

    store = RunStore()
    await store.connect()
    try:
        # 1.失败样本优先：error 名额 = limit * error_ratio（失败样本最需要被评测盯住）
        n_error = int(limit * max(0.0, min(1.0, error_ratio)))
        runs: list[Any] = []
        if n_error > 0:
            runs.extend(await store.fetch_runs(user_ids=None, status="error", limit=n_error))
        # 2.剩余名额给成功样本（取最新的）
        n_ok = limit - len(runs)
        if n_ok > 0:
            runs.extend(await store.fetch_runs(user_ids=None, status="finished", limit=n_ok))
        # 3.行对象转 dict，只留回灌要用的字段
        return [
            {
                "run_id": r["run_id"],
                "thread_id": r["thread_id"],
                "user_id": r["user_id"],
                "input_preview": r["input_preview"],
                "status": r["status"],
            }
            for r in runs
        ]
    finally:
        await store.close()


async def build_samples(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把采到的 run 转成评测样本：question=当时用户输入，reference=当时 agent 回答。

    从线程 checkpoint 历史里定位 input_preview 对应的用户消息，取其后第一条
    assistant 回答作 reference（旧版本输出 = 回归基准）；定位失败退回
    「最后一条 user + 其后第一条 assistant」；历史读取失败的单条跳过不阻断。
    """
    from harness.runtime.history.reader import get_message_history

    samples: list[dict[str, Any]] = []
    for r in runs:
        # 1.读线程历史；单条失败跳过，不阻断整批
        try:
            history = await get_message_history(r["user_id"], r["thread_id"], limit=200)
        except Exception as exc:  # noqa: BLE001 —— 单条失败不阻断
            logger.warning("run %s 历史读取失败（跳过）: %s", r["run_id"], exc)
            continue
        # 2.定位 (用户输入, 助手回答)；找不到就用 input_preview 兜底当问题
        question, reference = _locate_pair(history, r["input_preview"])
        if not question:
            question = r["input_preview"]
        if not question:
            continue
        samples.append({
            "question": question,
            "reference": reference,
            "run_id": r["run_id"],
            "status": r["status"],
        })
    return samples


def _locate_pair(history: list[dict[str, Any]], preview: str) -> tuple[str, str]:
    """在线程历史里定位 preview 对应的 (用户输入, 助手回答)。

    定位规则：用户消息包含 preview 前 24 字符 → 取其后第一条 assistant；
    找不到 → 兜底取最后一条 user 及其后第一条 assistant。

    返回：
        (question, reference)；都可能为空串（历史为空时）
    """
    is_user = lambda role: role in ("user", "human")
    is_assistant = lambda role: role in ("assistant", "ai", "chatbot")

    # 1.精确定位：包含 preview 片段的用户消息 + 其后第一条 assistant
    needle = (preview or "").strip()[:24]
    if needle:
        for i, m in enumerate(history):
            if is_user(m.get("role", "")) and needle in str(m.get("content", "")):
                for nxt in history[i + 1:]:
                    if is_assistant(nxt.get("role", "")):
                        return str(m.get("content", "")), str(nxt.get("content", ""))
                break
    # 2.兜底：最后一条 user + 其后第一条 assistant
    last_user_idx = max(
        (i for i, m in enumerate(history) if is_user(m.get("role", ""))), default=-1
    )
    if last_user_idx < 0:
        return "", ""
    question = str(history[last_user_idx].get("content", ""))
    reference = ""
    for nxt in history[last_user_idx + 1:]:
        if is_assistant(nxt.get("role", "")):
            reference = str(nxt.get("content", ""))
            break
    return question, reference


async def backfill(*, limit: int, error_ratio: float, dataset: str) -> int:
    """线上采样回灌主流程：采样 → 构造样本 → 上传独立数据集。

    参数：
        limit: 采样总条数
        error_ratio: 失败样本占比
        dataset: 目标数据集名（独立于手写 golden）

    返回：
        上传条数
    """
    from langsmith import Client

    from harness.eval.datasets import ensure_dataset

    # 1.采样线上 run（失败优先）
    runs = await sample_runs(limit=limit, error_ratio=error_ratio)
    if not runs:
        logger.warning("观测库没有可采样的 run（先跑几轮线上任务）")
        return 0
    logger.info("采样到 %d 条线上 run（error 优先）", len(runs))
    # 2.转评测样本（question=当时输入，reference=当时回答=回归基准）
    samples = await build_samples(runs)
    if not samples:
        logger.warning("没有构造出可用样本（历史读取全失败）")
        return 0
    # 3.上传独立数据集：线上样本无 golden 标注，facts/expected_tools 留空
    #   （correction 用 reference=旧输出打分 → 回归检测语义；faithfulness/工具选择空标注自动满分）
    examples = [
        {"question": s["question"], "reference": s["reference"], "facts": [], "expected_tools": []}
        for s in samples
    ]
    client = Client()
    ensure_dataset(client, dataset, examples)
    logger.info("回灌完成：%d 条样本 → 数据集 %s", len(samples), dataset)
    return len(samples)
