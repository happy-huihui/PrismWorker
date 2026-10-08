from __future__ import annotations

import json
import logging
import threading
from datetime import date, timedelta
from typing import Any

"""观测数据仓库 —— spans / logs 表的读写。

    职责：把 span（结构化调用树）与 log（结构化日志）持久化到 spans / logs 表，复用现有连接与线程隔离。
        - 建表：spans / logs + 索引，并给 runs 补 trace_id / total_tokens / cost 列
        - 写：insert_span / insert_log（单条或批量）
        - 读：fetch_spans / fetch_logs（供观测中台查询）

    对外暴露：
        - ObservabilityStore  观测库读写（connect / close / insert_span / insert_log /
                              fetch_spans / fetch_logs）
        - get_observability_store / reset_observability_store  进程级单例
"""

logger = logging.getLogger(__name__)

# 1.建 spans 表：一次 agent 执行里的结构化调用树（llm/tool/retrieval 等步骤）
_CREATE_SPANS_SQL = """
CREATE TABLE IF NOT EXISTS spans (
    span_id        TEXT PRIMARY KEY,
    parent_span_id TEXT,
    trace_id       TEXT NOT NULL,
    run_id         TEXT NOT NULL,
    thread_id      TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    type           TEXT NOT NULL,
    name           TEXT NOT NULL,
    started_at     REAL NOT NULL,
    finished_at    REAL,
    duration_ms    REAL,
    model_name     TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    total_tokens   INTEGER,
    cost           REAL,
    error          TEXT,
    attributes     TEXT NOT NULL DEFAULT '{}',
    created_at     REAL NOT NULL
)
"""

# 2.建 logs 表：structlog 结构化日志落库（供观测中台「日志流」聚合）
_CREATE_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    log_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id  TEXT NOT NULL DEFAULT '',
    run_id    TEXT NOT NULL DEFAULT '',
    span_id   TEXT NOT NULL DEFAULT '',
    level     TEXT NOT NULL,
    logger    TEXT NOT NULL,
    event     TEXT NOT NULL,
    fields    TEXT NOT NULL DEFAULT '{}',
    timestamp REAL NOT NULL
)
"""

# 3.建 evals 表：离线评测分数落库（分数双存：LangSmith UI + 本地观测中台评测页）
_CREATE_EVALS_SQL = """
CREATE TABLE IF NOT EXISTS evals (
    eval_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment  TEXT NOT NULL,
    question    TEXT NOT NULL,
    metric      TEXT NOT NULL,
    score       REAL NOT NULL,
    output      TEXT NOT NULL DEFAULT '',
    model_name  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
)
"""

# 4.runs 表补列（幂等，逐列 try/except 兼容旧库已存在的情况）
_RUN_ADD_COLUMNS = (
    ("trace_id", "TEXT NOT NULL DEFAULT ''"),
    ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cost", "REAL NOT NULL DEFAULT 0"),
)


class ObservabilityStore:
    """spans / logs 表仓库（aiosqlite；与 runs/checkpoints 同库、独立连接）。"""

    def __init__(self, *, db_path: Any | None = None) -> None:
        """初始化。

        参数：
            db_path: 库文件路径；None 时 connect() 内按全局配置解析（与 RunStore 同库）
        """
        self._db_path = db_path
        self._conn: Any = None

    @property
    def connected(self) -> bool:
        """是否已建立连接。"""
        return self._conn is not None

    async def connect(self) -> None:
        """建连接并建表/补列（幂等；已连接则直接返回）。"""
        # 1.已连接则复用
        if self._conn is not None:
            return
        # 2.未指定库路径 → 与 RunStore 同库解析（checkpoints.db）
        if self._db_path is None:
            from harness.memory.paths import resolve_memory_paths

            _, db_path = resolve_memory_paths(None)
            self._db_path = db_path
        import aiosqlite

        conn = await aiosqlite.connect(str(self._db_path))
        conn.row_factory = aiosqlite.Row
        # 3.建 spans / logs / evals 表 + 索引
        await conn.execute(_CREATE_SPANS_SQL)
        await conn.execute(_CREATE_LOGS_SQL)
        await conn.execute(_CREATE_EVALS_SQL)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_run ON spans(run_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_trace ON logs(trace_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_run ON logs(run_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_evals_experiment ON evals(experiment)")
        # 4.给 runs 表补观测列（旧库缺列时补齐；已存在则忽略 duplicate column）
        for column, definition in _RUN_ADD_COLUMNS:
            try:
                await conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            except Exception:  # noqa: BLE001 —— 列已存在属正常迁移路径
                pass
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        """关闭连接（幂等）。"""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def insert_span(self, span: dict[str, Any]) -> None:
        """写入一条 span（span_id 冲突则覆盖，保证重试幂等）。

        参数：
            span: span 字段字典（attributes 应为可 JSON 序列化的 dict）
        """
        await self._conn.execute(
            "INSERT INTO spans (span_id, parent_span_id, trace_id, run_id, thread_id, "
            "user_id, type, name, started_at, finished_at, duration_ms, model_name, "
            "input_tokens, output_tokens, total_tokens, cost, error, attributes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(span_id) DO UPDATE SET "
            "finished_at=excluded.finished_at, duration_ms=excluded.duration_ms, "
            "input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, "
            "total_tokens=excluded.total_tokens, cost=excluded.cost, error=excluded.error, "
            "attributes=excluded.attributes",
            (
                span["span_id"], span.get("parent_span_id"), span["trace_id"],
                span["run_id"], span["thread_id"], span["user_id"], span["type"],
                span["name"], span["started_at"], span.get("finished_at"),
                span.get("duration_ms"), span.get("model_name"),
                span.get("input_tokens"), span.get("output_tokens"),
                span.get("total_tokens"), span.get("cost"), span.get("error"),
                json.dumps(span.get("attributes") or {}, ensure_ascii=False),
                span.get("created_at") or span["started_at"],
            ),
        )
        await self._conn.commit()

    async def insert_log(self, log: dict[str, Any]) -> None:
        """写入一条结构化日志（事件落库，供中台日志流聚合）。

        参数：
            log: {trace_id, run_id, span_id, level, logger, event, fields, timestamp}
        """
        await self._conn.execute(
            "INSERT INTO logs (trace_id, run_id, span_id, level, logger, event, fields, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                log.get("trace_id", ""), log.get("run_id", ""), log.get("span_id", ""),
                log.get("level", "info"), log.get("logger", ""), log.get("event", ""),
                json.dumps(log.get("fields") or {}, ensure_ascii=False),
                log.get("timestamp") or 0.0,
            ),
        )
        await self._conn.commit()

    async def fetch_spans(self, run_id: str) -> list[dict[str, Any]]:
        """取某 run 的全部 span（按开始时间正序，供中台画调用树）。

        参数：
            run_id: 目标 run

        返回：
            span 字典列表（attributes 已反序列化为 dict）
        """
        cursor = await self._conn.execute(
            "SELECT * FROM spans WHERE run_id = ? ORDER BY started_at ASC", (run_id,)
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_span_row_to_dict(row) for row in rows]

    async def fetch_logs(
        self,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """按 trace_id / run_id 取结构化日志（时间正序，限量）。

        参数：
            trace_id / run_id: 过滤条件（至少给一个；都给则取并集语义 → 任一命中）
            limit: 最多返回条数

        返回：
            日志字典列表（fields 已反序列化为 dict）
        """
        # 1.拼条件：trace_id / run_id 任一命中即纳入（日志可能只带其中一种）
        conditions: list[str] = []
        params: list[Any] = []
        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if not conditions:
            return []
        where = " OR ".join(conditions)
        cursor = await self._conn.execute(
            f"SELECT * FROM logs WHERE {where} ORDER BY timestamp ASC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_log_row_to_dict(row) for row in rows]

    # ── 聚合查询（观测中台总览/分析页，真实 SQL 聚合）────────────────────
    async def overview_stats(
        self,
        *,
        since: float,
        until: float | None = None,
        user_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """总览指标：运行数/成功/失败/取消/活跃 + 总token/总成本 + P95 耗时。

        参数：
            since: 统计起点（epoch 秒）
            until: 统计终点（epoch 秒，None=到现在）；上一窗口对比时传前一窗口边界
            user_ids: 过滤用户 id 列表；None=全部（管理员）

        返回：
            {total, finished, errors, cancelled, active, tokens, cost, p95_duration}
        """
        where, params = _user_where(user_ids)
        # 0.终点过滤：默认到现在；传 until 时统计 [since, until) 半开区间（上一窗口对比用）
        until_clause = " AND created_at < ?" if until is not None else ""
        until_params = (until,) if until is not None else ()
        # 1.聚合计数（COUNT + 各状态 CASE 计数 + token/成本求和）
        cursor = await self._conn.execute(
            f"SELECT COUNT(*) AS total, "
            f"COALESCE(SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END), 0) AS finished, "
            f"COALESCE(SUM(CASE WHEN status='error' THEN 1 ELSE 0 END), 0) AS errors, "
            f"COALESCE(SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled, "
            f"COALESCE(SUM(CASE WHEN status IN ('running','pending') THEN 1 ELSE 0 END), 0) AS active, "
            f"COALESCE(SUM(total_tokens), 0) AS tokens, COALESCE(SUM(cost), 0) AS cost "
            f"FROM runs WHERE created_at >= ?{until_clause}{where}",
            (since, *until_params, *params),
        )
        row = await cursor.fetchone()
        await cursor.close()
        item = dict(row) if row else {}
        # 2.P95 耗时：取 finished 且带起止时间的 run 时长，Python 里算分位（sqlite 无 percentile）
        dur_cursor = await self._conn.execute(
            f"SELECT finished_at - started_at AS d FROM runs "
            f"WHERE status='finished' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            f"AND created_at >= ?{until_clause}{where}",
            (since, *until_params, *params),
        )
        durations = [r["d"] for r in await dur_cursor.fetchall()]
        await dur_cursor.close()
        # 3.输入/输出 token 拆分（runs 表只有总量，拆分来自 llm span）
        io_cursor = await self._conn.execute(
            f"SELECT COALESCE(SUM(input_tokens), 0) AS in_tokens, "
            f"COALESCE(SUM(output_tokens), 0) AS out_tokens "
            f"FROM spans WHERE type='llm' AND started_at >= ?{until_clause}{where}",
            (since, *until_params, *params),
        )
        io_row = await io_cursor.fetchone()
        await io_cursor.close()
        item["in_tokens"] = int(io_row["in_tokens"]) if io_row else 0
        item["out_tokens"] = int(io_row["out_tokens"]) if io_row else 0
        # 4.P50 / P95 耗时（sqlite 无 percentile，Python 线性插值）
        item["p50_duration"] = round(_percentile(durations, 0.5), 2) if durations else 0.0
        item["p95_duration"] = round(_percentile(durations, 0.95), 2) if durations else 0.0
        return item

    async def daily_series(
        self, *, since: float, user_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """按天聚合运行数与成本（补齐无数据的日期为 0，供趋势图）。

        参数：
            since / user_ids: 同 overview_stats

        返回：
            [{day: "YYYY-MM-DD", runs: int, cost: float}, ...] 按天升序
        """
        where, params = _user_where(user_ids)
        cursor = await self._conn.execute(
            f"SELECT date(created_at, 'unixepoch', 'localtime') AS day, "
            f"COUNT(*) AS runs, COALESCE(SUM(cost), 0) AS cost "
            f"FROM runs WHERE created_at >= ?{where} GROUP BY day ORDER BY day",
            (since, *params),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        # 1.有数据的天建立索引
        by_day = {r["day"]: r for r in rows}
        # 2.从 since 到今日逐天补 0，保证横轴连续
        today = date.today()
        d = date.fromtimestamp(since)
        series: list[dict[str, Any]] = []
        while d <= today:
            key = d.isoformat()
            hit = by_day.get(key)
            series.append({
                "day": key,
                "runs": int(hit["runs"]) if hit else 0,
                "cost": float(hit["cost"]) if hit else 0.0,
            })
            d += timedelta(days=1)
        return series

    async def model_aggregate(
        self, *, since: float, user_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """按模型聚合调用量/输入输出 token/成本/平均耗时（基于 llm span）。

        返回：
            [{model, calls, in_tokens, out_tokens, cost, avg_duration_ms}] 按成本降序
        """
        where, params = _user_where(user_ids)
        cursor = await self._conn.execute(
            f"SELECT model_name AS model, COUNT(*) AS calls, "
            f"COALESCE(SUM(input_tokens), 0) AS in_tokens, "
            f"COALESCE(SUM(output_tokens), 0) AS out_tokens, "
            f"COALESCE(SUM(cost), 0) AS cost, COALESCE(AVG(duration_ms), 0) AS avg_duration_ms "
            f"FROM spans WHERE type='llm' AND model_name IS NOT NULL AND started_at >= ?{where} "
            f"GROUP BY model_name ORDER BY cost DESC",
            (since, *params),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def tool_aggregate(
        self, *, since: float, user_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """按工具聚合调用次数/成功率/平均耗时（基于 tool span）。

        返回：
            [{tool, calls, success_rate, avg_duration_ms}] 按次数降序
        """
        where, params = _user_where(user_ids)
        cursor = await self._conn.execute(
            f"SELECT name, COUNT(*) AS calls, "
            f"COALESCE(SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END), 0) AS ok, "
            f"COALESCE(AVG(duration_ms), 0) AS avg_duration_ms "
            f"FROM spans WHERE type='tool' AND started_at >= ?{where} "
            f"GROUP BY name ORDER BY calls DESC",
            (since, *params),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        # 1.name 形如 "tool.web_search"，剥掉前缀；成功率 = ok/calls
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["tool"] = item["name"].removeprefix("tool.")
            item["success_rate"] = round(item["ok"] / item["calls"], 4) if item["calls"] else 0.0
            out.append(item)
        return out

    async def top_cost_runs(
        self, *, since: float, user_ids: list[str] | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        """取成本最高的 N 条 run（供「最贵 Top N」下钻）。

        返回：
            [{run_id, user_id, input_preview, model_name, cost, created_at}] 按成本降序
        """
        where, params = _user_where(user_ids)
        cursor = await self._conn.execute(
            f"SELECT run_id, user_id, input_preview, model_name, cost, created_at "
            f"FROM runs WHERE created_at >= ?{where} ORDER BY cost DESC LIMIT ?",
            (since, *params, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    # ── 评测分数读写（分数双存的本地侧）───────────────────────────
    async def insert_evals(self, rows: list[dict[str, Any]]) -> int:
        """批量写入评测分数（evals 表），返回写入条数。

        参数：
            rows: [{experiment, question, metric, score, output, model_name, created_at}]

        返回：
            写入条数
        """
        # 1.空列表直接返回，避免 executemany 空跑
        if not rows:
            return 0
        await self._conn.executemany(
            "INSERT INTO evals (experiment, question, metric, score, output, model_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["experiment"],
                    r["question"],
                    r["metric"],
                    float(r["score"]),
                    r.get("output") or "",
                    r.get("model_name") or "",
                    r.get("created_at") or 0.0,
                )
                for r in rows
            ],
        )
        await self._conn.commit()
        return len(rows)

    async def fetch_evals(
        self, *, experiment: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """取评测分数明细（可按实验过滤，eval_id 倒序）。"""
        if experiment:
            cursor = await self._conn.execute(
                "SELECT * FROM evals WHERE experiment = ? ORDER BY eval_id DESC LIMIT ?",
                (experiment, limit),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM evals ORDER BY eval_id DESC LIMIT ?", (limit,)
            )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]

    async def eval_summary(self) -> list[dict[str, Any]]:
        """评测聚合：按 (experiment, metric) 求条数与平均分（供中台版本对比）。"""
        cursor = await self._conn.execute(
            "SELECT experiment, metric, COUNT(*) AS n, AVG(score) AS avg_score, "
            "MAX(created_at) AS latest, MIN(model_name) AS model_name "
            "FROM evals GROUP BY experiment, metric ORDER BY latest DESC, metric"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(r) for r in rows]


def _user_where(user_ids: list[str] | None) -> tuple[str, list[str]]:
    """拼 user_id 过滤条件；None/空 = 全部用户（管理员），不追加过滤。

    返回：
        (WHERE 片段, 参数列表)
    """
    if not user_ids:
        return "", []
    placeholders = ", ".join("?" for _ in user_ids)
    return f" AND user_id IN ({placeholders})", list(user_ids)


def _percentile(values: list[float], p: float) -> float:
    """线性插值百分位（values 非空）。"""
    sorted_vals = sorted(values)
    rank = (len(sorted_vals) - 1) * p
    lower = int(rank)
    upper = min(lower + 1, len(sorted_vals) - 1)
    frac = rank - lower
    return sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac


def _span_row_to_dict(row: Any) -> dict[str, Any]:
    """spans 行 → dict（attributes JSON 反序列化，坏值降级空 dict）。"""
    item = dict(row)
    try:
        attributes = json.loads(item.get("attributes") or "{}")
    except (json.JSONDecodeError, TypeError):
        attributes = {}
    item["attributes"] = attributes if isinstance(attributes, dict) else {}
    return item


def _log_row_to_dict(row: Any) -> dict[str, Any]:
    """logs 行 → dict（fields JSON 反序列化，坏值降级空 dict）。"""
    item = dict(row)
    try:
        fields = json.loads(item.get("fields") or "{}")
    except (json.JSONDecodeError, TypeError):
        fields = {}
    item["fields"] = fields if isinstance(fields, dict) else {}
    return item


_observability_store: ObservabilityStore | None = None
_observability_store_lock = threading.Lock()


def get_observability_store() -> ObservabilityStore:
    """返回进程级 ObservabilityStore 单例（懒构造，线程安全；供中台查询）。"""
    global _observability_store
    if _observability_store is not None:
        return _observability_store
    with _observability_store_lock:
        if _observability_store is None:
            _observability_store = ObservabilityStore()
        return _observability_store


def reset_observability_store() -> None:
    """清空单例（测试隔离用：下一个 get_observability_store 重新构造）。"""
    global _observability_store
    with _observability_store_lock:
        _observability_store = None
