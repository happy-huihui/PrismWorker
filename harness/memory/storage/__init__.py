from __future__ import annotations

from harness.memory.storage.document import (
    MemoryRevisionConflict,
    MemoryStorage,
    MemoryStorageCorruption,
    MemoryStorageError,
    create_empty_memory,
    utc_now_iso_z,
)
from harness.memory.storage.migration import migrate_legacy_sqlite

"""持久化包子路径（storage）

    职责：长期记忆 memory.json 的落盘与旧库迁移。
    内容：document（文档读写 + 乐观锁）、migration（旧 SQLite 迁移）。

    对外暴露：
        - MemoryStorage / create_empty_memory / utc_now_iso_z / 三个错误类
        - migrate_legacy_sqlite
"""

__all__ = [
    "MemoryStorage",
    "MemoryStorageError",
    "MemoryStorageCorruption",
    "MemoryRevisionConflict",
    "create_empty_memory",
    "utc_now_iso_z",
    "migrate_legacy_sqlite",
]
