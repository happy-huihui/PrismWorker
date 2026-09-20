from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from harness.memory.config import PrismMemConfig
from harness.memory.paths import memory_root
from harness.memory.storage.document import (
    MemoryStorage,
    _fact_id,
    create_empty_memory,
    utc_now_iso_z,
)

logger = logging.getLogger(__name__)

"""旧长期记忆库迁移（storage.migration）

    职责：把历史遗留的 SQLite 记忆库（memory.db / memory_entries 表）
         一次性导入为新的 memory.json 文档，并把旧库改名备份。
    规则：
        - 仅当旧库存在、且某用户还没有 memory.json 时才导入该用户；
        - 旧行按 kind 映射为事实类别，confidence=1.0（历史数据视为确定），source="legacy"；
        - 全部成功后旧库改名 memory.db.migrated.bak；失败只告警不阻断（下次启动重试）。
"""

# 旧库文件名与表名
_LEGACY_DB_FILE = "memory.db"
_LEGACY_TABLE = "memory_entries"


def migrate_legacy_sqlite(mem_config: PrismMemConfig) -> int:
    """把旧 SQLite 长期记忆库导入新文档。

    参数：
        mem_config: 后端私有配置（决定记忆根目录）

    返回：
        成功生成的新文档数量（0 = 无旧库 / 无数据 / 全部已迁移）
    """
    # 1.旧库不存在直接跳过
    root = memory_root(mem_config)
    db_path = root / _LEGACY_DB_FILE
    if not db_path.is_file():
        return 0

    # 2.打开旧库（失败只告警，不阻断记忆启动）
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        logger.warning("旧长期记忆库打开失败，跳过迁移: %s", exc)
        return 0
    # 3.读旧行（无 memory_entries 表 = 无需迁移）
    try:
        rows = conn.execute(
            f"SELECT user_id, key, content, kind FROM {_LEGACY_TABLE} ORDER BY updated_at"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.info("旧库无 memory_entries 表，无需迁移: %s", exc)
        rows = []
    finally:
        conn.close()

    if not rows:
        return 0

    from harness.memory.paths import safe_user_id

    # 4.按用户分组（user_id 归一到 default 兜底）
    by_user: dict[str, list[tuple[str, str, str]]] = {}
    for user_id, key, content, kind in rows:
        by_user.setdefault(str(user_id or "default"), []).append(
            (str(key or ""), str(content or ""), str(kind or "general"))
        )

    # 5.逐用户生成新文档（已有 memory.json 的用户跳过，不动其数据）
    storage = MemoryStorage(mem_config)
    migrated = 0
    for raw_uid, entries in by_user.items():
        uid = safe_user_id(raw_uid)
        if storage._document_path(uid).exists():
            continue  # 已有新文档，不动旧数据
        document = create_empty_memory()
        now = utc_now_iso_z()
        # 6.内容去重后追加为事实
        seen: set[str] = set()
        for key, content, kind in entries:
            if not content:
                continue
            content_key = _content_key(content)
            if content_key in seen:
                continue
            seen.add(content_key)
            document["facts"].append(
                {
                    "id": _fact_id(),
                    "content": content,
                    "category": _legacy_kind_to_category(kind),
                    "confidence": 1.0,
                    "createdAt": now,
                    "source": "legacy",
                }
            )
        # 7.有事实才落盘；单用户失败不阻断其余
        if not document["facts"]:
            continue
        try:
            storage.save(document, user_id=uid, expected_revision=0)
            migrated += 1
        except Exception as exc:  # noqa: BLE001 —— 单用户迁移失败不阻断其余
            logger.warning("用户 %s 的旧记忆迁移失败: %s", uid, exc)

    # 8.有迁移成功才备份旧库（改名，保留原始数据可回滚）
    if migrated:
        backup = db_path.with_name(f"{_LEGACY_DB_FILE}.migrated.bak")
        try:
            os.replace(db_path, backup)
            logger.info(
                "旧长期记忆库迁移完成：%d 个用户文档已生成，旧库备份为 %s",
                migrated,
                backup,
            )
        except OSError as exc:
            logger.warning("旧库备份失败（保留原库）: %s", exc)
    return migrated


def _content_key(content: str) -> str:
    """事实内容归一化键（去空白、小写），用于去重。"""
    return " ".join(content.strip().lower().split())


def _legacy_kind_to_category(kind: str) -> str:
    """旧 kind 开放标签映射为事实类别：已知类别直通，未知归 context。"""
    normalized = (kind or "").strip().lower()
    if normalized in {"preference", "knowledge", "context", "behavior", "goal", "correction"}:
        return normalized
    return "context"
