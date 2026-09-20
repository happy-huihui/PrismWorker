from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.memory.config import PrismMemConfig
from harness.memory.paths import memory_file_path, memory_root

logger = logging.getLogger(__name__)

"""长期记忆文档存储（storage.document）

    职责：每用户一份 memory.json 的读写 + revision 乐观锁 + 原子落盘 + 线程安全。
    数据模型（与参考实现一致）：
        {
          "version": "1.0",
          "revision": 0,                     # 乐观锁版本号，每次写入 +1
          "lastUpdated": "...Z",
          "user":    {workContext/personalContext/topOfMind: {summary, updatedAt}},
          "history": {recentMonths/earlierContext/longTermBackground: {...}},
          "facts":   [{id, content, category, confidence, createdAt, source}]
        }
    设计要点：
        - 进程内 RLock 保证线程安全（队列 Timer 线程与主线程并发）；
        - 写入「临时文件 + os.replace」原子替换，杜绝半写文件；
        - save 校验 expected_revision，冲突抛 MemoryRevisionConflict 由 updater 重试；
        - agent_name 仅保留签名兼容，存储一律按 user 分桶。
"""

# fact id 合法字符集（外部传入 id 时校验用）
_FACT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class MemoryStorageError(RuntimeError):
    """存储层错误基类。"""


class MemoryStorageCorruption(MemoryStorageError):
    """持久化的 memory.json 无法安全读取。"""


class MemoryRevisionConflict(MemoryStorageError):
    """写入时 revision 与磁盘当前版本不一致（乐观锁冲突）。"""


def utc_now_iso_z() -> str:
    """当前 UTC 时间的 ISO8601 字符串（Z 结尾，参考实现的统一时区格式）。"""
    # 去掉 +00:00 偏移尾串，替换为 Z，保持与历史数据同一格式
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


def _fact_id() -> str:
    """生成短事实 id（与参考实现同风格：fact_ + hex 8）。"""
    return f"fact_{uuid.uuid4().hex[:8]}"


class MemoryStorage:
    """memory.json 文档存储（revision 乐观锁 + 原子写 + 线程锁）。"""

    def __init__(self, config: PrismMemConfig):
        """初始化存储；config 决定根目录与文件位置。

        参数：
            config: 后端私有配置（storage_path 决定记忆根目录）
        """
        self._config = config
        # 确保记忆根目录存在（一次性）
        self._root = memory_root(config)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ── 内部工具 ────────────────────────────────────────────────────────
    def _document_path(self, user_id: str) -> Path:
        """返回某用户的 memory.json 路径（父目录不自动创建）。"""
        return memory_file_path(self._config, user_id)

    def _read_raw(self, user_id: str) -> dict[str, Any] | None:
        """读取磁盘上的原始文档；不存在返回 None，损坏抛 MemoryStorageCorruption。

        参数：
            user_id: 用户

        返回：
            文档 dict；文件不存在返回 None
        """
        path = self._document_path(user_id)
        # 1.文件不存在视作「还没有记忆」
        if not path.is_file():
            return None
        # 2.读文本，IO 失败=损坏
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemoryStorageCorruption(f"读取记忆文档失败 {path}: {exc}") from exc
        # 3.解析 JSON，坏 JSON=损坏
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MemoryStorageCorruption(f"记忆文档 JSON 损坏 {path}: {exc}") from exc
        # 4.顶层必须是对象，否则=损坏
        if not isinstance(document, dict):
            raise MemoryStorageCorruption(f"记忆文档结构非法 {path}: 顶层不是 JSON 对象")
        return document

    def _atomic_write(self, user_id: str, document: dict[str, Any]) -> None:
        """临时文件 + os.replace 原子写（父目录自动创建）。

        参数：
            user_id: 目标用户
            document: 待写入文档
        """
        path = self._document_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写同目录临时文件，再原子替换，杜绝读到半写文件
        tmp = path.with_suffix(".json.tmp")
        raw = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
        tmp.write_bytes(raw)
        os.replace(tmp, path)

    # ── 文档级操作 ──────────────────────────────────────────────────────
    def load(self, user_id: str) -> dict[str, Any]:
        """读取用户记忆文档；不存在返回空文档（无副作用）。

        参数：
            user_id: 用户

        返回：
            归一化后的记忆文档
        """
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

        参数：
            document: 待保存文档
            user_id: 目标用户
            expected_revision: 调用方读到的版本号

        返回：
            True 表示成功

        异常：
            版本不一致抛 MemoryRevisionConflict（由 updater 重读重试）
        """
        with self._lock:
            # 1.读当前磁盘版本
            current = self._read_raw(user_id)
            current_rev = int(current.get("revision") or 0) if current else 0
            # 2.版本不符即冲突，不覆盖
            if expected_revision != current_rev:
                raise MemoryRevisionConflict(
                    f"预期 revision={expected_revision}，磁盘当前 revision={current_rev}"
                    f"（user={user_id}）；请重读后重试"
                )
            # 3.写回：版本 +1、刷新时间戳、原子落盘
            document["revision"] = current_rev + 1
            document["lastUpdated"] = utc_now_iso_z()
            self._atomic_write(user_id, document)
            return True

    def reload(self, user_id: str) -> dict[str, Any]:
        """强制重读（丢弃任何缓存视图）；与 load 同义（本项目无缓存）。"""
        return self.load(user_id)

    def clear(self, *, user_id: str, agent_name: str | None = None) -> dict[str, Any]:
        """清空某用户的整份记忆文档，返回清空后的文档。

        参数：
            user_id: 目标用户
            agent_name: 兼容签名；单 Agent 架构下即清空该用户全部记忆
        """
        with self._lock:
            document = create_empty_memory()
            self._atomic_write(user_id, document)
            return document

    def close(self) -> None:
        """释放资源（本实现无外部资源，保持接口一致）。"""

    # ── 防御性归一化 ────────────────────────────────────────────────────
    def _normalize_document(self, document: dict[str, Any]) -> dict[str, Any]:
        """补齐缺失的分区字段，保证下游（updater/注入）总能安全访问。

        参数：
            document: 从磁盘读出的原始文档

        返回：
            补齐 version/revision/user/history/facts 后的副本
        """
        empty = create_empty_memory()
        result = copy.deepcopy(document)
        # 1.顶层基础字段兜底
        result.setdefault("version", "1.0")
        result.setdefault("revision", 0)
        result.setdefault("lastUpdated", "")
        # 2.user / history 每个子格若不是 dict 就用空模板替换
        for section in ("user", "history"):
            result.setdefault(section, {})
            for key, fallback in empty[section].items():
                value = result[section].get(key)
                if not isinstance(value, dict):
                    result[section][key] = copy.deepcopy(fallback)
        # 3.facts 必须是列表
        if not isinstance(result.get("facts"), list):
            result["facts"] = []
        return result
