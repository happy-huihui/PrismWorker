from __future__ import annotations

import json
import queue
import sqlite3
import sys
import threading
import time
from typing import Any

"""结构化日志落库 sink（db_sink）——把日志事件异步批量写入 logs 表。

    职责：
        1.后台线程 + 有界队列，把 structlog 结构化事件异步批量写进 logs 表（checkpoints.db）
        2.只落 INFO 及以上；队列满丢弃计数、写失败丢本批，绝不阻塞主链路。

    对外暴露：
        - db_sink_processor    structlog processor：把事件入队（供 DB handler 挂载）
        - start_db_sink / stop_db_sink   启停后台写线程
        - get_db_sink         单例
"""

# 日志级别 → 数值（只落 INFO 及以上，DEBUG 不进库）
_LEVEL_RANK = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "warn": 30,
    "error": 40,
    "critical": 50,
}

# 队列容量上限（满则丢弃并计数）
_QUEUE_MAXSIZE = 10000
# 批量写入条数上限
_BATCH_MAX = 200
# 空闲 flush 间隔(秒)
_FLUSH_INTERVAL = 1.0

# logs 表建表语句（与 store.py 的 _CREATE_LOGS_SQL 保持一致，独立连接自建表幂等）
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

# 事件字典里「单独成列」的已知字段；其余键都归入 fields
_KNOWN_FIELDS = frozenset({
    "timestamp", "level", "logger", "event",
    "trace_id", "run_id", "span_id", "thread_id", "user_id",
    "_record", "_from_structlog",
})


def _json_safe(value: Any) -> Any:
    """把任意值递归转成 JSON 可序列化形态（兜底降级为 str）。"""
    # 1.标量与 None 原样返回
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # 2.容器递归清洗
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    # 3.其余类型（对象/异常等）降级为 str，保证一定能序列化
    return str(value)


class DBLogSink:
    """后台写库 sink：结构化事件入队，独立线程批量落 logs 表。"""

    def __init__(self, *, queue_maxsize: int = _QUEUE_MAXSIZE) -> None:
        """初始化；队列 + 停止信号 + 后台线程占位。"""
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_maxsize)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: sqlite3.Connection | None = None
        self._resolve_failed = False
        self._dropped = 0

    # ── 入队（由 structlog processor 调用）────────────────────────
    def enqueue(self, event_dict: dict[str, Any]) -> None:
        """把一条结构化事件入队（只落 INFO 及以上；满则丢弃计数）。

        参数：
            event_dict: structlog 加工后的事件字典
        """
        # 1.级别过滤：DEBUG 不进库
        level = str(event_dict.get("level") or "info").lower()
        if _LEVEL_RANK.get(level, 20) < _LEVEL_RANK["info"]:
            return
        # 2.规范化成 logs 表一行，非阻塞入队
        try:
            self._queue.put_nowait(self._normalize(event_dict))
        except queue.Full:
            self._dropped += 1

    def _normalize(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """把事件字典规范化成 logs 表一行（已知字段成列、其余进 fields）。"""
        # 1.已知字段单独成列
        fields = {k: v for k, v in event_dict.items() if k not in _KNOWN_FIELDS}
        return {
            "trace_id": str(event_dict.get("trace_id") or ""),
            "run_id": str(event_dict.get("run_id") or ""),
            "span_id": str(event_dict.get("span_id") or ""),
            "level": str(event_dict.get("level") or "info").lower(),
            "logger": str(event_dict.get("logger") or ""),
            "event": str(event_dict.get("event") or ""),
            # 2.其余字段收进 fields（JSON 友好化）
            "fields": _json_safe(fields),
            # 3.时间戳用入队时刻（epoch 秒），与 spans 口径一致
            "timestamp": time.time(),
        }

    # ── 后台线程生命周期 ─────────────────────────────────────────
    def start(self) -> None:
        """启动后台写线程（幂等；已启动则直接返回）。"""
        # 1.已有线程在跑则不重复启动
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="prism-db-log-sink", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停后台线程：置停止信号、join、flush 剩余、关连接（幂等）。"""
        # 1.置停止信号并等线程退出（带超时，避免卡死关闭流程）
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        # 2.兜底 flush：线程可能没来得及写剩余，这里再写一轮
        self._flush_remaining()
        # 3.关连接
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 —— 关闭失败忽略
                pass
            self._conn = None

    def _worker(self) -> None:
        """后台线程主循环：按批取出队列并落库，停止信号到来时清空退出。"""
        while not self._stop.is_set():
            # 1.阻塞等一条（超时=空闲 flush 心跳，也用于及时响应 stop）
            try:
                first = self._queue.get(timeout=_FLUSH_INTERVAL)
            except queue.Empty:
                continue
            # 2.凑批：拿到首条后尽量把队列里已有的一并取走
            batch = [first]
            while len(batch) < _BATCH_MAX:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            self._write_batch(batch)
        # 3.退出前清空剩余（stop 时可能还有未写事件）
        self._flush_remaining()

    def _flush_remaining(self) -> None:
        """把队列里剩余事件全部取出并落库（非阻塞）。"""
        remaining: list[dict[str, Any]] = []
        while True:
            try:
                remaining.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if remaining:
            self._write_batch(remaining)

    # ── 写库 ─────────────────────────────────────────────────────
    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        """把一批事件 executemany 写进 logs 表（失败丢本批，不重试）。"""
        conn = self._ensure_conn()
        if conn is None:
            return
        try:
            conn.executemany(
                "INSERT INTO logs (trace_id, run_id, span_id, level, logger, event, fields, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        rec["trace_id"], rec["run_id"], rec["span_id"], rec["level"],
                        rec["logger"], rec["event"],
                        json.dumps(rec["fields"], ensure_ascii=False), rec["timestamp"],
                    )
                    for rec in batch
                ],
            )
            conn.commit()
        except Exception:  # noqa: BLE001 —— 失败丢本批，不重试避免卡死后台线程
            pass

    def _ensure_conn(self) -> sqlite3.Connection | None:
        """惰性建同步 sqlite3 连接并建表（失败降级：只落文件不落库）。"""
        # 1.已建连接直接复用
        if self._conn is not None:
            return self._conn
        # 2.解析失败过就不再重试（避免每次都抛异常刷屏）
        if self._resolve_failed:
            return None
        # 3.解析库路径（与 spans/runs 同库）→ 建连接 + 建表 + 索引
        try:
            from harness.memory.paths import resolve_memory_paths

            _, db_path = resolve_memory_paths(None)
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            conn.execute(_CREATE_LOGS_SQL)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_trace ON logs(trace_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_run ON logs(run_id)")
            conn.commit()
            self._conn = conn
            return conn
        except Exception as exc:  # noqa: BLE001 —— 库不可用则降级为仅文件
            self._resolve_failed = True
            # 这里不能用 logging（会递归入队），直接打到 stderr
            print(f"[observability] 日志落库初始化失败，仅落文件: {exc}", file=sys.stderr)
            return None


_sink: DBLogSink | None = None
_sink_lock = threading.Lock()


def get_db_sink() -> DBLogSink:
    """返回进程级 DBLogSink 单例（懒构造，线程安全）。"""
    global _sink
    if _sink is not None:
        return _sink
    with _sink_lock:
        if _sink is None:
            _sink = DBLogSink()
        return _sink


def start_db_sink() -> None:
    """启动落库后台线程（幂等）。"""
    get_db_sink().start()


def stop_db_sink() -> None:
    """停止落库后台线程并 flush 剩余。"""
    if _sink is not None:
        _sink.stop()


def db_sink_processor(_logger: Any, _method: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor：把事件入队（副作用），原样返回事件字典。

    参数：
        _logger / _method: structlog 处理器约定签名，此处不用
        event_dict: 待输出的日志事件

    返回：
        原样返回的 event_dict（不改变渲染结果）
    """
    get_db_sink().enqueue(event_dict)
    return event_dict
