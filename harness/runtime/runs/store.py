from __future__ import annotations

import json
import logging
from typing import Any

from harness.runtime.runs.models import (
    RUN_COLUMNS,
    RUN_STATUS_ERROR,
    RUN_STATUS_PENDING,
    RUN_STATUS_RUNNING,
    RunRecord,
)

"""run 记录仓库

    职责：把一次 run 的元信息持久化到 runs 表（与 checkpointer 同库、独立连接）
        - 建表 + 连接管理 + 插入 / 更新 / 查询
        - 行 → RunRecord 映射；不含编排逻辑

    对外暴露：
        - RunStore          run 表读写（connect / close / insert / update / fetch_row / fetch_rows / fetch_chains）
        - row_to_record     行 → RunRecord
"""

logger = logging.getLogger(__name__)

# 建表 SQL（幂等；artifacts / events 存 JSON 字符串）
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    thread_id     TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    status        TEXT NOT NULL,
    model_name    TEXT NOT NULL DEFAULT '',
    input_preview TEXT NOT NULL DEFAULT '',
    error         TEXT,
    artifacts     TEXT NOT NULL DEFAULT '[]',
    events        TEXT NOT NULL DEFAULT '[]',
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    trace_id      TEXT NOT NULL DEFAULT '',
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    cost          REAL NOT NULL DEFAULT 0
)
"""


class RunStore:
    """runs 表读写仓库（aiosqlite；与 checkpointer 同库、独立连接）。"""

    def __init__(self, *, db_path: Any | None = None) -> None:
        """初始化。

        参数：
            db_path: 库文件路径；None 时 connect() 内按全局配置解析
        """
        # 连接延迟到 connect() 再建：构造不碰 IO，便于单测注入库路径
        self._db_path = db_path
        self._conn: Any = None

    @property
    def connected(self) -> bool:
        """是否已建立连接。"""
        # 只表示「已连接」，不代表表已建好（建表在 connect 内完成）
        return self._conn is not None

    async def connect(self) -> None:
        """建连接并建表（幂等；已连接则直接返回）。"""
        # 已连接则复用
        if self._conn is not None:
            return
        # 未指定库路径 → 与 checkpointer 同库解析
        if self._db_path is None:
            from harness.memory.paths import resolve_memory_paths

            _, db_path = resolve_memory_paths(None)
            self._db_path = db_path
        import aiosqlite

        conn = await aiosqlite.connect(str(self._db_path))
        conn.row_factory = aiosqlite.Row
        await conn.execute(_CREATE_TABLE_SQL)
        # 旧库迁移：逐列补齐（已存在则忽略 duplicate column 报错）。
        # events 是思考链回放列；trace_id/total_tokens/cost 是观测列（批 2/3 新增）。
        # 这里必须迁移：fetch_runs 的 SELECT 走 RUN_COLUMNS 白名单，缺列会直接 500。
        for column, definition in (
            ("events", "TEXT NOT NULL DEFAULT '[]'"),
            ("trace_id", "TEXT NOT NULL DEFAULT ''"),
            ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("cost", "REAL NOT NULL DEFAULT 0"),
        ):
            try:
                await conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            except Exception:  # noqa: BLE001 —— 列已存在属正常
                pass
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_thread ON runs(thread_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)")
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        """关闭连接（幂等）。"""
        # 幂等关闭：重复调用安全
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def insert(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        status: str,
        model_name: str,
        input_preview: str,
        created_at: float,
        trace_id: str = "",
    ) -> None:
        """插入一条 pending run 记录。

        参数：
            run_id / thread_id / user_id / status / model_name / input_preview /
            created_at / trace_id: 初始字段（error/artifacts/message_count 走默认）
        """
        # 只写初始字段，其余（error / artifacts / 计数 / 时间）留默认，收尾时再 update
        await self._conn.execute(
            "INSERT INTO runs (run_id, thread_id, user_id, status, model_name, "
            "input_preview, error, artifacts, message_count, created_at, "
            "started_at, finished_at, trace_id) VALUES (?, ?, ?, ?, ?, ?, NULL, '[]', 0, ?, NULL, NULL, ?)",
            (run_id, thread_id, user_id, status, model_name, input_preview, created_at, trace_id),
        )
        await self._conn.commit()

    async def mark_stale_runs_interrupted(self, *, now: float) -> int:
        """把上次进程遗留的非终态 run 标记为 error（服务重启中断）。

        参数：
            now: 中断时间戳（统一记到 finished_at）

        返回：
            受影响行数（供启动恢复记日志）
        """
        # 1.只动 running/pending（进程重启后内存句柄已消失，这些必是孤儿）；
        #    finished/cancelled/error 等终态保持原样不动
        cursor = await self._conn.execute(
            "UPDATE runs SET status = ?, error = ?, finished_at = ? "
            "WHERE status IN (?, ?)",
            (
                RUN_STATUS_ERROR,
                "服务重启，运行中断（进程退出前的未完成任务）",
                now,
                RUN_STATUS_RUNNING,
                RUN_STATUS_PENDING,
            ),
        )
        await self._conn.commit()
        # 2.返回受影响行数，供调用方决定是否告警
        return cursor.rowcount

    async def update(self, run_id: str, *, fields: dict[str, Any]) -> None:
        """按字段更新一条 run 记录（只允许 RUN_COLUMNS 内字段）。

        参数：
            run_id: 目标 run
            fields: 待更新字段（键须在白名单内，否则忽略）
        """
        if not fields:
            return
        # 白名单过滤，避免拼进非法列名（events 为思考链回放列，允许更新）
        allowed = set(RUN_COLUMNS) | {"events"}
        safe_fields = {k: v for k, v in fields.items() if k in allowed}
        if not safe_fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in safe_fields)
        await self._conn.execute(
            f"UPDATE runs SET {assignments} WHERE run_id = ?",
            (*safe_fields.values(), run_id),
        )
        await self._conn.commit()

    async def fetch_row(self, run_id: str) -> Any:
        """按 run_id 取一行（aiosqlite.Row；无则 None）。

        参数：
            run_id: 目标 run

        返回：
            行对象或 None
        """
        # 只取白名单列，避免 SELECT * 把 events 这种大字段一并拖出来
        cursor = await self._conn.execute(
            f"SELECT {', '.join(RUN_COLUMNS)} FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetch_rows(
        self,
        *,
        user_id: str | None,
        thread_id: str | None,
        limit: int,
    ) -> list[Any]:
        """按条件查询多行（倒序，限量）。

        参数：
            user_id / thread_id: 过滤条件（None 表示不过滤该项）
            limit: 最多返回条数

        返回：
            行对象列表（created_at 倒序）
        """
        conditions: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if thread_id is not None:
            conditions.append("thread_id = ?")
            params.append(thread_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self._conn.execute(
            f"SELECT {', '.join(RUN_COLUMNS)} FROM runs {where} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    async def fetch_runs(
        self,
        *,
        user_ids: list[str] | None = None,
        status: str | None = None,
        model_name: str | None = None,
        q: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Any]:
        """按条件分页查询 runs（观测中台列表用，created_at 倒序）。

        参数：
            user_ids: 用户过滤（None = 全部，管理员用）
            status / model_name: 状态/模型过滤（None 不过滤）
            q: 关键词搜索（请求预览 / trace_id / run_id 子串 LIKE）
            from_ts / to_ts: 创建时间范围（epoch 秒）
            limit / offset: 分页

        返回：
            行对象列表（含 total_tokens / cost / trace_id 观测列）
        """
        # 1.拼过滤条件（统一走 _run_filters，与 count_runs 口径一致，防注入）
        where, params = _run_filters(
            user_ids=user_ids,
            status=status,
            model_name=model_name,
            q=q,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        # 2.分页查询（倒序，limit/offset 由调用方控制）
        cursor = await self._conn.execute(
            f"SELECT {', '.join(RUN_COLUMNS)} FROM runs {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)

    async def count_runs(
        self,
        *,
        user_ids: list[str] | None = None,
        status: str | None = None,
        model_name: str | None = None,
        q: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
    ) -> int:
        """统计符合条件的 run 总数（供列表分页；过滤口径与 fetch_runs 一致）。"""
        where, params = _run_filters(
            user_ids=user_ids,
            status=status,
            model_name=model_name,
            q=q,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        cursor = await self._conn.execute(f"SELECT COUNT(*) AS c FROM runs {where}", params)
        row = await cursor.fetchone()
        await cursor.close()
        return int(row["c"]) if row else 0


    async def fetch_chains(
        self,
        *,
        user_id: str,
        thread_id: str,
        limit: int = 100,
    ) -> list[Any]:
        """取某线程全部 run 的思考链事件（按创建时间正序，供历史回放）。

        参数：
            user_id / thread_id: 归属过滤
            limit: 最多返回条数

        返回：
            行对象列表（含 run_id/status/model_name/started_at/finished_at/events）
        """
        # 按创建时间正序，前端历史回放才能还原真实先后
        cursor = await self._conn.execute(
            "SELECT run_id, status, model_name, created_at, started_at, finished_at, events "
            "FROM runs WHERE user_id = ? AND thread_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (user_id, thread_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)


    async def fetch_events(self, run_id: str) -> list[dict[str, Any]]:
        """取某 run 的思考链事件（runs.events 列，JSON 反序列化）。

        参数：
            run_id: 目标 run

        返回：
            事件列表 [{event, data}, ...]；无记录或坏值返回空列表
        """
        cursor = await self._conn.execute(
            "SELECT events FROM runs WHERE run_id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return []
        try:
            events = json.loads(row["events"] or "[]")
        except (json.JSONDecodeError, TypeError):
            events = []
        return events if isinstance(events, list) else []


def _run_filters(
    *,
    user_ids: list[str] | None,
    status: str | None,
    model_name: str | None,
    q: str | None,
    from_ts: float | None,
    to_ts: float | None,
) -> tuple[str, list[Any]]:
    """拼 runs 列表/计数共用的 WHERE 过滤（全走 ? 占位防注入）。

    返回：
        (WHERE 片段, 参数列表)
    """
    conditions: list[str] = []
    params: list[Any] = []
    if user_ids:
        conditions.append(f"user_id IN ({', '.join('?' for _ in user_ids)})")
        params.extend(user_ids)
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if model_name is not None:
        conditions.append("model_name = ?")
        params.append(model_name)
    if q:
        like = f"%{q}%"
        conditions.append("(input_preview LIKE ? OR trace_id LIKE ? OR run_id LIKE ?)")
        params.extend([like, like, like])
    if from_ts is not None:
        conditions.append("created_at >= ?")
        params.append(from_ts)
    if to_ts is not None:
        conditions.append("created_at <= ?")
        params.append(to_ts)
    return (f"WHERE {' AND '.join(conditions)}" if conditions else "", params)


def row_to_record(row: Any) -> RunRecord:
    """DB 行 → RunRecord（兼容 aiosqlite.Row）。

    参数：
        row: 查询返回的行

    返回：
        对应的 RunRecord（artifacts JSON 解析失败归空列表）
    """
    item = dict(row)
    # artifacts 列是 JSON 字符串，坏值降级为空列表
    try:
        artifacts = json.loads(item.get("artifacts") or "[]")
    except (json.JSONDecodeError, TypeError):
        artifacts = []
    artifact_list: list[str] = [str(a) for a in artifacts] if isinstance(artifacts, list) else []
    return RunRecord(
        run_id=item["run_id"],
        thread_id=item["thread_id"],
        user_id=item["user_id"],
        status=item["status"],
        model_name=item.get("model_name") or "",
        input_preview=item.get("input_preview") or "",
        error=item.get("error"),
        artifacts=artifact_list,
        message_count=int(item.get("message_count") or 0),
        created_at=float(item.get("created_at") or 0.0),
        started_at=float(item["started_at"]) if item.get("started_at") is not None else None,
        finished_at=float(item["finished_at"]) if item.get("finished_at") is not None else None,
        # 观测字段：旧库/未汇总行缺列时回退空值
        trace_id=item.get("trace_id") or "",
        total_tokens=int(item.get("total_tokens") or 0),
        cost=float(item.get("cost") or 0.0),
    )
