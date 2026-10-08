from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

"""评测 CLI 入口（__main__）——python -m harness.eval。

    职责：把离线评测串成一条命令：装配全真 agent → 跑 LangSmith aevaluate() →
        用评测器打分 → 收尾。链路真、数据真、分数真。
        注意：langgraph dev 只做 Studio 可视化，真正的测评走这条 CLI。

    对外暴露：
        - main   命令行入口（返回进程退出码）
"""

logger = logging.getLogger(__name__)


async def _run_agent(agent: Any, question: str, timeout_seconds: float = 120.0) -> dict[str, Any]:
    """用全真 agent 跑一条输入，返回 {output, tools_used}。

    参数：
        agent: 全真 agent 图
        question: 输入问题
        timeout_seconds: 单条超时（写代码类任务在沙箱里可能慢/循环，超时兜底不拖垮整轮）
    """
    import uuid

    from langchain_core.messages import HumanMessage

    # 1.每条示例独立线程：checkpointer 按 config.thread_id 分线程，混用会串会话
    thread_id = f"eval-{uuid.uuid4().hex[:12]}"
    # 2.超时兜底：agent 循环/沙箱慢时单条超时返回占位，不卡死整轮评测
    try:
        async with asyncio.timeout(timeout_seconds):
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=question)]},
                config={"configurable": {"thread_id": thread_id}},
            )
    except TimeoutError:
        logger.warning("示例超时（%ss）：%s", timeout_seconds, question[:40])
        return {"output": f"[评测超时 {timeout_seconds}s]", "tools_used": [], "artifacts": []}
    messages = result.get("messages", []) if isinstance(result, dict) else []
    # 1.输出正文 = 最后一条 AI 消息的内容
    output = ""
    for m in reversed(messages):
        if getattr(m, "type", None) == "ai":
            content = getattr(m, "content", "")
            output = content if isinstance(content, str) else str(content)
            break
    # 2.工具清单 = 遍历所有 AI 消息的 tool_calls 去重
    tools_used: list[str] = []
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name and name not in tools_used:
                tools_used.append(name)
    # 3.产物清单 = state.artifacts（present_files 登记的交付路径，复杂任务完成度评测要用）
    artifacts = list(result.get("artifacts") or []) if isinstance(result, dict) else []
    return {"output": output, "tools_used": tools_used, "artifacts": artifacts}


async def _run(args: argparse.Namespace) -> int:
    """执行一次离线评测：装配 → evaluate → 清理。"""
    from langsmith import Client
    from langsmith.evaluation import aevaluate

    # 1.装配全真 agent（沙箱 + checkpointer + 全中间件/工具）
    from harness.eval.agent import build_eval_agent

    agent, sandbox, checkpointer = await build_eval_agent(
        model_name=args.model,
        thinking_enabled=args.thinking,
    )

    # 2.裁判模型 + 数据集 + 评测器
    from harness.eval.datasets import ensure_dataset
    from harness.eval.evaluators import (
        build_correctness,
        build_faithfulness,
        build_safety_refusal,
        build_task_completion,
        tool_selection,
        trajectory,
    )
    from harness.eval.judge import create_judge_model

    judge = create_judge_model(args.judge)
    client = Client()
    dataset = ensure_dataset(client, args.dataset)
    # --limit N：按 task_type 均衡取 N 条（覆盖基础/复杂/边界），方便快速验证；0=全量
    data_arg: Any = (
        _balanced_examples(client, dataset, args.limit)
        if args.limit and args.limit > 0
        else args.dataset
    )
    # 3.可读实验名：显式传 --experiment 用原名；否则 模型+时间 自动生成（区别于 LangSmith 随机后缀）
    experiment_name = _make_experiment_name(args)

    async def target(inputs: dict[str, Any]) -> dict[str, Any]:
        """LangSmith 目标函数：一条 dataset 输入 → 一份 agent 输出。"""
        return await _run_agent(agent, inputs["question"], timeout_seconds=args.timeout)

    # 4.离线评测：跑数据集 → 4 评测器打分 → 写 experiment（可在 UI 对比版本）
    evaluators = [
        build_correctness(judge),
        build_faithfulness(judge),
        tool_selection,
        trajectory,
        build_task_completion(judge),
        build_safety_refusal(judge),
    ]
    try:
        result = await aevaluate(
            target,
            data=data_arg,
            evaluators=evaluators,
            experiment_prefix=experiment_name,
            max_concurrency=args.concurrency,
        )
        logger.info("评测完成: %s", getattr(result, "experiment_name", "?"))
        # 5.分数双存：从结果收集 (experiment/question/metric/score) 回写本地 evals 表
        await _write_back_evals(
            result, model_name=args.model or "auto", experiment_name=experiment_name
        )
    finally:
        # 4.收尾：关 checkpointer 连接（限时）+ 沙箱回源热池（同步，与 worker 同款）
        if checkpointer is not None and hasattr(checkpointer, "conn"):
            try:
                # 限时关闭：asyncio.timeout 取消 ainvoke 后可能残留未完成的 checkpoint 写，
                # conn.close 会等它 → 限 10s，超时放弃（评测数据已 commit 落盘，不差这一下）
                await asyncio.wait_for(checkpointer.conn.close(), timeout=10)
            except Exception:  # noqa: BLE001 —— 关闭失败/超时不影响主流程
                logger.warning("checkpointer 关闭失败（忽略）", exc_info=True)
        try:
            from harness.runtime.sandbox import release_app_sandbox

            # AioSandbox.close() 是同步方法，正确姿势是回源热池（release，同步）
            release_app_sandbox(
                sandbox_id=str(sandbox.id) if sandbox is not None else None
            )
        except Exception:  # noqa: BLE001 —— 回源失败不影响主流程
            logger.warning("沙箱回源热池失败（忽略）", exc_info=True)
    return 0


def _load_env() -> None:
    """加载项目根 .env（模型密钥等），不覆盖已有环境变量。

    与 app.runner 的 _load_dotenv 同责，但这里用 python-dotenv（项目已有依赖），
    显式 utf-8 解码，避免 Windows 默认 GBK 读中文注释出错。
    """
    from dotenv import load_dotenv

    # 显式 utf-8 + 不覆盖已有环境变量（与 runner 语义一致）
    load_dotenv(encoding="utf-8", override=False)


async def _write_back_evals(result: Any, *, model_name: str, experiment_name: str) -> None:
    """把 LangSmith 评测结果回写本地 evals 表（分数双存：LangSmith UI + 本地中台）。

    参数：
        result: aevaluate 返回的 AsyncExperimentResults
        model_name: 被评 agent 的模型档名（None 时记 auto）
        experiment_name: 可读实验名（本地 evals 表用它，而非 LangSmith 的随机后缀名）
    """
    import time

    # 1.遍历结果行，展开成 (experiment/question/metric/score/output) 平铺记录
    rows: list[dict[str, Any]] = []
    async for row in result:
        run = row.get("run")
        example = row.get("example")
        question = (example.inputs or {}).get("question", "") if example is not None else ""
        output = (run.outputs or {}).get("output", "") if run is not None else ""
        eval_results = (row.get("evaluation_results") or {}).get("results") or []
        for er in eval_results:
            score = getattr(er, "score", None)
            if score is None:
                continue
            rows.append({
                "experiment": experiment_name,
                "question": question,
                "metric": getattr(er, "key", ""),
                "score": float(score),
                "output": output,
                "model_name": model_name,
                "created_at": time.time(),
            })
    # 2.无结果直接跳过（不落空行）
    if not rows:
        logger.warning("评测结果为空，跳过本地回写")
        return
    # 3.写入本地 evals 表（与 spans/logs 同库，供观测中台评测页读）
    from harness.observability.store import get_observability_store

    store = get_observability_store()
    if not store.connected:
        await store.connect()
    n = await store.insert_evals(rows)
    logger.info("评测分数已回写本地 %d 条", n)


def _make_experiment_name(args: argparse.Namespace) -> str:
    """生成可读实验名：显式传 --experiment 用原名；否则 模型+时间 自动生成。"""
    import datetime

    # 1.显式实验名优先（用户当作版本标识，稳定可复跑）
    if args.experiment:
        return args.experiment
    # 2.未传则 模型+时间 自动生成，每次跑可区分
    model = args.model or "auto"
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    return f"{model}-{ts}"


def _balanced_examples(client: Any, dataset: Any, n: int) -> list[Any]:
    """从数据集按 task_type 均衡取前 n 条（覆盖基础/复杂/边界三类）。

    为什么均衡：数据集按类别顺序排列（基础在前），直接取前 n 条会全是基础题，
    小规模验证覆盖不到复杂/边界。按类别轮询取样，保证一次小跑能覆盖三类。

    参数：
        client: langsmith.Client 实例
        dataset: LangSmith dataset 对象
        n: 目标条数

    返回：
        均衡取样后的 example 列表
    """
    all_examples = list(client.list_examples(dataset_id=dataset.id))
    # 1.按 task_type 分组
    by_type: dict[str, list[Any]] = {}
    for ex in all_examples:
        t = str((ex.outputs or {}).get("task_type") or "other")
        by_type.setdefault(t, []).append(ex)
    # 2.各类轮询取，直到凑够 n 条（某类取完就跳过）
    picked: list[Any] = []
    while len(picked) < n and any(by_type.values()):
        for t in list(by_type):
            if by_type[t] and len(picked) < n:
                picked.append(by_type[t].pop(0))
    return picked


def main(argv: list[str] | None = None) -> int:
    """命令行入口：加载 .env → 解析参数 → 异步跑评测 → 返回退出码。"""
    # 1.先加载 .env：评测装配要读模型密钥（与 app.runner 的 _load_dotenv 同责）
    _load_env()
    parser = argparse.ArgumentParser(
        prog="python -m harness.eval",
        description="PrismWorker 离线评测（LangSmith）",
    )
    parser.add_argument("--dataset", default="prismworker-golden", help="LangSmith 数据集名")
    parser.add_argument("--experiment", default=None, help="可读实验名（版本标识）；不传则 模型+时间 自动生成")
    parser.add_argument("--model", default=None, help="模型档名；默认走配置路由")
    parser.add_argument("--thinking", action="store_true", help="开启思考模式")
    parser.add_argument("--judge", default=None, help="裁判模型档名；默认 mimo-flash")
    parser.add_argument("--concurrency", type=int, default=2, help="并发度")
    parser.add_argument("--timeout", type=float, default=120.0, help="单条示例超时(秒)，默认 120")
    parser.add_argument("--limit", type=int, default=0, help="只跑 N 条（按类型均衡取样，0=全量）")
    # 线上采样回灌（批 3）：传 --backfill N 时走回灌流程，不跑评测
    parser.add_argument("--backfill", type=int, default=None, metavar="N",
                        help="线上采样回灌：采 N 条线上 run 转评测样本（失败优先），不跑评测")
    parser.add_argument("--error-ratio", type=float, default=0.5, help="回灌时失败样本占比（默认 0.5）")
    parser.add_argument("--backfill-dataset", default="prismworker-online",
                        help="回灌目标数据集（独立于手写 golden，默认 prismworker-online）")
    args = parser.parse_args(list(argv) if argv is not None else None)
    # 回灌模式：--backfill N 走「采样线上 run → 回灌数据集」，不跑评测
    if args.backfill is not None:
        code = asyncio.run(_run_backfill(args))
    else:
        code = asyncio.run(_run(args))
    # 兜底强退：评测/回灌数据已 commit 落盘。asyncio.timeout 取消 ainvoke 时会残留
    # 同步沙箱 exec 的孤儿线程，asyncio.run 收尾会一直等它 → 直接 os._exit 跳过等待。
    # （先 flush 再退，保证 print/日志不丢。）
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


async def _run_backfill(args: argparse.Namespace) -> int:
    """线上采样回灌：runs 表采样（失败优先）→ 构造样本 → 上传独立数据集。"""
    from harness.eval.backfill import backfill

    n = await backfill(
        limit=args.backfill,
        error_ratio=args.error_ratio,
        dataset=args.backfill_dataset,
    )
    print(f"回灌完成：{n} 条样本 → 数据集 {args.backfill_dataset}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
