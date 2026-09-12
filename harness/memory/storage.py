"""长期记忆存储层：memory.json 文档的读写与乐观锁。

数据模型（每用户一份文档，与参考实现一致）：
    {
      "version": "1.0",
      "revision": 0,              # 乐观锁版本号，每次写入 +1
      "lastUpdated": "...Z",
      "user": {workContext/personalContext/topOfMind: {summary, updatedAt}},
      "history": {recentMonths/earlierContext/longTermBackground: {...}},
      "facts": [{id, content, category, confidence, createdAt, source}]
    }

设计要点：
    - 单进程场景：进程内 RLock 保证线程安全（队列 Timer 线程与主线程并发）；
    - 每次写入「临时文件 + os.replace」原子替换，杜绝半写文件；
    - 保存时校验 expected_revision，冲突抛 MemoryRevisionConflict，
      由 updater 重读重试（增量更新不丢不重）；
    - agent_name 参数保留在接口上以兼容签名，但本项目为单 Agent 架构，
      存储一律按 user 分桶（fact.source 记录来源线程即可回溯）；
    - 首次初始化检测旧 SQLite 库（memory.db / memory_entries 表），
      把旧记忆导入为新文档的 facts 并备份旧库，迁移失败只告警不阻断。
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.memory.config import PrismMemConfig
from harness.memory.paths import memory_file_path, memory_root

logger = logging.getLogger(__name__)


class MemoryStorageError(RuntimeError):
    """存储层错误基类。"""


class MemoryStorageCorruption(MemoryStorageError):
    """持久化的 memory.json 无法安全读取。"""


class MemoryRevisionConflict(MemoryStorageError):
    """写入时 revision 与磁盘当前版本不一致（乐观锁冲突）。"""


def utc_now_iso_z() -> str:
    """当前 UTC 时间的 ISO8601 字符串（Z 结尾，参考实现的统一时区格式）。"""
    return datetime.now(UTC).isoformat().removesuffix("+00:00") + "Z"


def create_empty_memory() -> dict[str, Any]:
    """返回空记忆文档（updater 与注入共用同一结构）。"""
    return {
        "version": "1.0",
        "revision": 0,
        "lastUpdated": utc_now_iso_z(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


_FACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _fact_id() -> str:
    """生成短事实 id（与参考实现同风格：fact_ + hex 8）。"""
    return f"fact_{uuid.uuid4().hex[:8]}"


class MemoryStorage:
    """memory.json 文档存储（revision 乐观锁 + 原子写 + 线程锁）。"""

    def __init__(self, config: PrismMemConfig):
        """初始化存储；config 决定根目录与文件位置。"""
        self._config = config
        self._root = memory_root(config)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ── 内部工具 ────────────────────────────────────────────────────────
    def _document_path(self, user_id: str) -> Path:
        """返回某用户的 memory.json 路径（父目录不自动创建）。"""
        return memory_file_path(self._config, user_id)

    def _read_raw(self, user_id: str) -> dict[str, Any] | None:
        """读取磁盘上的原始文档；不存在返回 None，损坏抛异常。"""
        path = self._document_path(user_id)
        if not path.is_file():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemoryStorageCorruption(f"读取记忆文档失败 {path}: {exc}") from exc
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemoryStorageCorruption(f"记忆文档 JSON 损坏 {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise MemoryStorageCorruption(f"记忆文档结构非法 {path}: 顶层不是 JSON 对象")
        return document

    def _atomic_write(self, user_id: str, document: dict[str, Any]) -> None:
        """临时文件 + os.replace 原子写（父目录自动创建）。"""
        path = self._document_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        raw = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
        tmp.write_bytes(raw)
        os.replace(tmp, path)

    # ── 文档级操作 ──────────────────────────────────────────────────────
    def load(self, user_id: str) -> dict[str, Any]:
        """读取用户记忆文档；不存在返回空文档（无副作用）。"""
        with self._lock:
            document = self._read_raw(user_id)
            if document is None:
                return create_empty_memory()
            # 防御损坏字段：缺关键结构时补空（不崩溃主链路）
            return self._normalize_document(document)

    def save(
        self,
        document: dict[str, Any],
        *,
        user_id: str,
        expected_revision: int,
    ) -> bool:
        """乐观锁保存：磁盘当前 revision 必须等于 expected_revision。

        Returns:
            True 表示保存成功；冲突抛 MemoryRevisionConflict。
        """
        with self._lock:
            current = self._read_raw(user_id)
            current_rev = int(current.get("revision") or 0) if current else 0
            if expected_revision != current_rev:
                raise MemoryRevisionConflict(
                    f"预期 revision={expected_revision}，磁盘当前 revision={current_rev}"
                    f"（user={user_id}）；请重读后重试"
                )
            document["revision"] = current_rev + 1
            document["lastUpdated"] = utc_now_iso_z()
            self._atomic_write(user_id, document)
            return True

    def reload(self, user_id: str) -> dict[str, Any]:
        """强制重读（丢弃任何缓存视图）；与 load 同义（本项目无缓存）。"""
        return self.load(user_id)

    def clear(self, *, user_id: str, agent_name: str | None = None) -> dict[str, Any]:
        """清空某用户的整份记忆文档，返回清空后的文档。

        agent_name 参数保留兼容签名；单 Agent 架构下即清空该用户全部记忆。
        """
        with self._lock:
            document = create_empty_memory()
            self._atomic_write(user_id, document)
            return document

    def close(self) -> None:
        """释放资源（本实现无外部资源，保持接口一致）。"""

    # ── 防御性归一化 ────────────────────────────────────────────────────
    def _normalize_document(self, document: dict[str, Any]) -> dict[str, Any]:
        """补齐缺失的分区字段，保证下游（updater/注入）总能安全访问。"""
        empty = create_empty_memory()
        result = copy.deepcopy(document)
        result.setdefault("version", "1.0")
        result.setdefault("revision", 0)
        result.setdefault("lastUpdated", "")
        for section in ("user", "history"):
            result.setdefault(section, {})
            for key, fallback in empty[section].items():
                value = result[section].get(key)
                if not isinstance(value, dict):
                    result[section][key] = copy.deepcopy(fallback)
        if not isinstance(result.get("facts"), list):
            result["facts"] = []
        return result


# ── 旧 SQLite 库迁移 ─────────────────────────────────────────────────────
_LEGACY_DB_FILE = "memory.db"
_LEGACY_TABLE = "memory_entries"


def migrate_legacy_sqlite(mem_config: PrismMemConfig) -> int:
    """把旧 SQLite 长期记忆库（memory.db / memory_entries 表）导入新文档。

    规则：
        - 仅当旧库存在、且某用户还没有 memory.json 时导入该用户；
        - 旧行按 kind（general/preference/fact/...）映射为事实类别，
          confidence 取 1.0（历史数据视为确定），source="legacy"；
        - 全部导入成功后把旧库改名备份 memory.db.migrated.bak，
          失败只告警不阻断（下次启动重试）。

    Returns:
        成功生成的新文档数量。
    """
    root = memory_root(mem_config)
    db_path = root / _LEGACY_DB_FILE
    if not db_path.is_file():
        return 0

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        logger.warning("旧长期记忆库打开失败，跳过迁移: %s", exc)
        return 0
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

    by_user: dict[str, list[tuple[str, str, str]]] = {}
    for user_id, key, content, kind in rows:
        by_user.setdefault(str(user_id or "default"), []).append(
            (str(key or ""), str(content or ""), str(kind or "general"))
        )

    storage = MemoryStorage(mem_config)
    migrated = 0
    for raw_uid, entries in by_user.items():
        uid = safe_user_id(raw_uid)
        if storage._document_path(uid).exists():
            continue  # 已有新文档，不动旧数据
        document = create_empty_memory()
        now = utc_now_iso_z()
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
        if not document["facts"]:
            continue
        try:
            storage.save(document, user_id=uid, expected_revision=0)
            migrated += 1
        except Exception as exc:  # noqa: BLE001 —— 单用户迁移失败不阻断其余
            logger.warning("用户 %s 的旧记忆迁移失败: %s", uid, exc)

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