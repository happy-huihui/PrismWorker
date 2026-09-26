from __future__ import annotations

import json
import logging
from typing import Any

from harness.runtime.runs.models import RUN_COLUMNS, RunRecord

"""run 记录仓库（runs.store）

    职责：把一次 run 的元信息持久化到 runs 表（与 checkpointer 同库、独立连接）。
    内容：建表 + 连接管理 + 插入/更新/查询 + 行→RunRecord 映射；不含编排逻辑。

    对外暴露：
        - RunStore  run 表读写（connect / close / insert / update / fetch_row / fetch_rows）

    输出数据示例（runs 表一行）：
        {run_id, thread_id, user_id, status, model_name, input_preview,
         error, artifacts(JSON 字符串), message_count, created_at,
         started_at, finished_at}
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
    finished_at   REAL
)
"""


class RunStore:
    """runs 表读写仓库（aiosqlite；与 checkpointer 同库、独立连接）。"""

    def __init__(self, *, db_path: Any | None = None) -> None:
        """初始化。

        参数：
            db_path: 库文件路径；None 时 connect() 内按全局配置解析
        """
        self._db_path = db_path
        self._conn: Any = None

    @property
    def connected(self) -> bool:
        """是否已建立连接。"""
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
        # 旧库迁移：补 events 列（已存在则忽略 duplicate column 报错）
        try:
            await conn.execute("ALTER TABLE runs ADD COLUMN events TEXT NOT NULL DEFAULT '[]'")
        except Exception:  # noqa: BLE001 —— 列已存在属正常
            pass
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_thread ON runs(thread_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)")
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        """关闭连接（幂等）。"""
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
    ) -> None:
        """插入一条 pending run 记录。

        参数：
            run_id / thread_id / user_id / status / model_name / input_preview /
            created_at: 初始字段（error/artifacts/message_count 走默认）
        """
        await self._conn.execute(
            "INSERT INTO runs (run_id, thread_id, user_id, status, model_name, "
            "input_preview, error, artifacts, message_count, created_at, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, NULL, '[]', 0, ?, NULL, NULL)",
            (run_id, thread_id, user_id, status, model_name, input_preview, created_at),
        )
        await self._conn.commit()

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
        cursor = await self._conn.execute(
            "SELECT run_id, status, model_name, created_at, started_at, finished_at, events "
            "FROM runs WHERE user_id = ? AND thread_id = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (user_id, thread_id, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)


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
    )
