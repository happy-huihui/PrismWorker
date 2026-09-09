"""长期记忆存储：基于标准库 sqlite3 的 MemoryStore。

单一文件 memory.db、一张表 memory_entries：
    - 复合主键 (user_id, key)：同一用户同一 key 重复保存 = 更新（upsert）
    - kind 是开放主题标签（preference / project / fact / episodic …），
      仅用于按类别过滤检索，代码不校验枚举
路径解析统一走 resolve_memory_paths()，manager 与 checkpointer 共用，
保证「记忆与 checkpoint 落在同一个 root_dir」。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.config.memory_config import MemoryConfig
from harness.config.paths import get_paths

logger = logging.getLogger(__name__)

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class MemoryEntry:
    """一条长期记忆（结构化的行数据）。"""

    user_id: str
    key: str
    content: str
    kind: str
    created_at: str
    updated_at: str


def _now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串（同格式保证按字典序即按时间序）。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_memory_paths(config: MemoryConfig | None = None) -> tuple[Path, Path]:
    """解析记忆库与 checkpoint 库两个文件的完整路径。

    Args:
        config: 记忆配置；缺省用全局配置。

    Returns:
        (memory.db 路径, checkpoints.db 路径)；父目录不存在时自动创建。
    """
    if config is None:
        from harness.config.app_config import get_app_config

        config = get_app_config().memory
    if config.root_dir:
        root = Path(config.root_dir).resolve()
    else:
        root = get_paths().base_dir / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / config.memory_db_file, root / config.checkpoints_db_file


class MemoryStore:
    """长期记忆存取（SQLite）。

    线程安全策略：每次操作开独立短连接——sqlite3 连接不被跨线程共享，
    短连接天然规避；记忆是低频读写，连接开销可忽略。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        table_name: str = "memory_entries",
        max_memory_chars: int = 2000,
    ):
        """初始化存储并确保表结构存在。

        Args:
            db_path: memory.db 文件路径。
            table_name: 记忆表名。
            max_memory_chars: 单条记忆内容上限（超长截断）。
        """
        if _TABLE_NAME_RE.fullmatch(table_name) is None:
            raise ValueError(f"非法的记忆表名: {table_name!r}")
        self._db_path = Path(db_path)
        self._table_name = table_name
        self._max_memory_chars = max_memory_chars
        self._initialize()


    def _initialize(self) -> None:
        """建表与索引（CREATE IF NOT EXISTS，幂等）。"""
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    user_id    TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    kind       TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                )
                """
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._table_name}_updated "
                f"ON {self._table_name} (user_id, updated_at DESC)"
            )

    @contextmanager
    def _connect(self) -> Any:
        """打开独立连接（WAL 提升读写并发，行工厂返回字典式行）。

        with 块退出时自动 commit + close——注意 Python 的
        ``with sqlite3.connect()`` 本身只管理事务不关连接，这里是
        显式管理生命周期，避免 Windows 上句柄未释放文件被占用。
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


    def save(
        self,
        user_id: str,
        key: str,
        content: str,
        *,
        kind: str = "general",
    ) -> bool:
        """保存一条记忆；同 user_id+key 已存在则更新内容与分类。

        Args:
            user_id: 记忆归属用户。
            key: 记忆唯一键。
            content: 记忆正文（超过上限自动截断）。
            kind: 主题标签，默认 general。

        Returns:
            True 表示新建，False 表示覆盖了已有记忆。
        """
        now = _now_iso()
        content = content[: self._max_memory_chars] if content else ""
        user_id = user_id or "default"
        key = (key or "").strip()
        if not key:
            raise ValueError("记忆 key 不能为空")
        with self._connect() as conn:
            existed = (
                conn.execute(
                    f"SELECT 1 FROM {self._table_name} WHERE user_id=? AND key=?",
                    (user_id, key),
                ).fetchone()
                is not None
            )
            conn.execute(
                f"INSERT INTO {self._table_name} (user_id, key, content, kind, created_at, updated_at) "
                f"VALUES (?, ?, ?, ?, ?, ?) "
                f"ON CONFLICT(user_id, key) DO UPDATE SET "
                f"content=excluded.content, kind=excluded.kind, updated_at=excluded.updated_at",
                (user_id, key, content, kind, now, now),
            )
        return not existed

    def search(
        self,
        user_id: str,
        query: str | None = None,
        *,
        kind: str | None = None,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """按关键词检索长期记忆（content/key 的 LIKE 匹配）。

        Args:
            user_id: 归属用户。
            query: 关键词；None 表示不限关键词（列出该用户全部记忆）。
            kind: 只取该标签下的记忆；None 表示不限。
            limit: 最多返回条数（至少 1）。

        Returns:
            匹配的记忆列表，按最近更新倒序。
        """
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id or "default"]
        if query:
            like = f"%{query}%"
            clauses.append("(content LIKE ? OR key LIKE ?)")
            params += [like, like]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        params.append(max(1, int(limit)))
        sql = (
            f"SELECT user_id, key, content, kind, created_at, updated_at "
            f"FROM {self._table_name} "
            f"WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [MemoryEntry(**dict(row)) for row in rows]

    def get(self, user_id: str, key: str) -> MemoryEntry | None:
        """按 (user_id, key) 精确取一条记忆；不存在返回 None。"""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT user_id, key, content, kind, created_at, updated_at "
                f"FROM {self._table_name} WHERE user_id=? AND key=?",
                (user_id, key),
            ).fetchone()
        return MemoryEntry(**dict(row)) if row is not None else None

    def delete(self, user_id: str, key: str) -> bool:
        """删除一条长期记忆。

        Args:
            user_id: 归属用户。
            key: 要删除的记忆键。

        Returns:
            True 表示真的删掉了，False 表示不存在。
        """
        with self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM {self._table_name} WHERE user_id=? AND key=?",
                (user_id, key),
            )
        return cursor.rowcount > 0



_store_lock: threading.Lock = threading.Lock()
_default_store: MemoryStore | None = None


def get_memory_store(config: MemoryConfig | None = None) -> MemoryStore:
    """返回进程级长期记忆存储单例（懒初始化 + 幂等建表）。

    Args:
        config: 记忆配置；缺省用全局配置。
    """
    global _default_store
    if _default_store is not None:
        return _default_store
    with _store_lock:
        if _default_store is None:
            if config is None:
                from harness.config.app_config import get_app_config

                config = get_app_config().memory
            db_path, _ = resolve_memory_paths(config)
            _default_store = MemoryStore(
                db_path,
                table_name=config.table_name,
                max_memory_chars=config.max_memory_chars,
            )
            logger.info("长期记忆库就绪: %s", db_path)
    return _default_store