from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_observability_store, get_run_service, require_admin
from app.api.schemas import ObsRunDetailOut, ObsRunListOut, run_out_from_record

"""观测中台路由（observability）——运行列表 / 详情 / 总览 / 分析。

    职责：把落库的观测数据（runs 汇总 + spans 调用树 + logs 日志流 + 思考链）以只读
        API 暴露给观测中台前端；总览/分析返回真实 SQL 聚合结果。
        所有端点仅管理员可用（require_admin 鉴权），数据范围为全部用户。

    对外暴露：
        - router  /observability 前缀的路由器
"""

router = APIRouter(prefix="/observability", tags=["observability"])

# 时间窗口 → 天数（today=当天，7d/30d=近 N 天，all=不限）
_RANGE_DAYS = {"7d": 7, "30d": 30}


def _resolve_range(range_: str) -> tuple[float, float]:
    """把时间窗口参数解析成 (from_ts, to_ts) epoch 秒区间。

    参数：
        range_: today / 7d / 30d / all（默认 7d）

    返回：
        (起点, 终点)；today 起点=当日零点，all 起点=0（全量），其余=now - N 天；终点均为 now
    """
    now = time.time()
    # 1.当天：起点取本地当日零点
    if range_ == "today":
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return start, now
    # 2.全部：不设下限（epoch 0）
    if range_ == "all":
        return 0.0, now
    # 3.近 N 天
    days = _RANGE_DAYS.get(range_, 7)
    return now - days * 86400, now


@router.get("/runs", response_model=ObsRunListOut)
async def list_observability_runs(
    limit: int = Query(default=15, ge=1, le=200, description="每页条数"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    status: str | None = Query(default=None, description="按状态过滤"),
    model_name: str | None = Query(default=None, description="按模型名过滤"),
    q: str | None = Query(default=None, description="关键词（请求预览/trace_id/run_id）"),
    range_: str = Query(default="7d", alias="range", description="时间窗口 today/7d/30d"),
    _admin: str = Depends(require_admin),
    service: Any = Depends(get_run_service),
) -> ObsRunListOut:
    """观测运行列表（管理员、全量用户；含搜索/时间/状态筛选 + 分页）。"""
    from_ts, to_ts = _resolve_range(range_)
    # 1.分页查询 + 总数（供前端真实分页）
    records = await service.list_observability_runs(
        user_ids=None,
        status=status,
        model_name=model_name,
        q=q,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        offset=offset,
    )
    total = await service.count_observability_runs(
        user_ids=None,
        status=status,
        model_name=model_name,
        q=q,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return ObsRunListOut(items=[run_out_from_record(record) for record in records], total=total)


@router.get("/runs/{run_id}", response_model=ObsRunDetailOut)
async def get_observability_run(
    run_id: str,
    _admin: str = Depends(require_admin),
    service: Any = Depends(get_run_service),
    obs_store: Any = Depends(get_observability_store),
) -> ObsRunDetailOut:
    """观测运行详情：run 汇总 + span 调用树 + 日志流 + 思考链回放。"""
    # 1.run 汇总（含 token/成本）；不存在报 404
    record = await service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"run 不存在: {run_id}")
    # 2.span 调用树 + 结构化日志 + 思考链事件
    spans = await obs_store.fetch_spans(run_id)
    logs = await obs_store.fetch_logs(run_id=run_id, limit=500)
    chain_events = await service.get_run_events(run_id)
    return ObsRunDetailOut(
        run=run_out_from_record(record),
        spans=spans,
        logs=logs,
        chain_events=chain_events,
    )


@router.get("/stats")
async def get_observability_stats(
    range_: str = Query(default="7d", alias="range", description="时间窗口 today/7d/30d/all"),
    _admin: str = Depends(require_admin),
    obs_store: Any = Depends(get_observability_store),
) -> dict[str, Any]:
    """总览：核心指标（含上一窗口对比）+ 按天趋势 + 模型调用占比。"""
    from_ts, _ = _resolve_range(range_)
    # 1.核心指标（含 P50/P95 耗时 + 输入/输出拆分）+ 派生成功率
    overview = await obs_store.overview_stats(since=from_ts, user_ids=None)
    total = int(overview.get("total") or 0)
    finished = int(overview.get("finished") or 0)
    overview["success_rate"] = round(finished / total * 100, 1) if total else 0.0
    # 2.上一窗口对比（总览卡「较上期」涨跌用）：today 比昨天同时长，7d/30d 比上一窗口，all 无对比
    overview_prev = None
    if range_ != "all":
        if range_ == "today":
            prev_from, prev_to = from_ts - 86400, from_ts
        else:
            window = _RANGE_DAYS.get(range_, 7) * 86400
            prev_from, prev_to = from_ts - window, from_ts
        overview_prev = await obs_store.overview_stats(
            since=prev_from, until=prev_to, user_ids=None
        )
        prev_total = int(overview_prev.get("total") or 0)
        prev_finished = int(overview_prev.get("finished") or 0)
        overview_prev["success_rate"] = (
            round(prev_finished / prev_total * 100, 1) if prev_total else 0.0
        )
    # 3.按天趋势（补齐日期）+ 模型调用占比
    daily = await obs_store.daily_series(since=from_ts, user_ids=None)
    models = await obs_store.model_aggregate(since=from_ts, user_ids=None)
    return {"overview": overview, "overview_prev": overview_prev, "daily": daily, "models": models}


@router.get("/analytics")
async def get_observability_analytics(
    range_: str = Query(default="7d", alias="range", description="时间窗口 today/7d/30d"),
    _admin: str = Depends(require_admin),
    obs_store: Any = Depends(get_observability_store),
) -> dict[str, Any]:
    """分析：模型聚合 + 工具排行 + 每日成本 + 最贵 Top N。"""
    from_ts, _ = _resolve_range(range_)
    models = await obs_store.model_aggregate(since=from_ts, user_ids=None)
    tools = await obs_store.tool_aggregate(since=from_ts, user_ids=None)
    daily = await obs_store.daily_series(since=from_ts, user_ids=None)
    top_cost = await obs_store.top_cost_runs(since=from_ts, user_ids=None, limit=5)
    return {"models": models, "tools": tools, "daily": daily, "top_cost": top_cost}


@router.get("/evals")
async def get_observability_evals(
    experiment: str | None = Query(default=None, description="按实验过滤（None=全部）"),
    _admin: str = Depends(require_admin),
    obs_store: Any = Depends(get_observability_store),
) -> dict[str, Any]:
    """评测分数（分数双存的本地侧）：实验汇总 + 明细。"""
    summary = await obs_store.eval_summary()
    details = await obs_store.fetch_evals(experiment=experiment, limit=500)
    return {"summary": summary, "details": details}
