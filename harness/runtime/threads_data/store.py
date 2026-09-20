from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from harness.config.paths import _validate_thread_id, get_paths

"""线程元数据仓库（threads_data.store）

    职责：只管线程的元数据（标题/时间/消息数/状态），不创建也不删除磁盘目录。
    背景：线程的 user-data 目录由 harness 在首次 run 时经 ThreadContextMiddleware
         自动创建；删除时做「目录 + 元数据」联动——先异步删 user-data 目录
         （失败则中止、保留元数据，保证「有 meta 必有目录可用」），成功后再删
         元数据行。
    存储：{base_dir}/users/{user_id}/threads/meta.json，单文件 JSON + 原子改写
         （临时文件 + os.replace），进程内缓存 + 写时落盘。

    对外暴露：
        - ThreadMeta       一条线程元数据
        - ThreadStore      仓库本体（create/get/list/rename/update/exists/delete）
        - get_thread_store 进程级单例
        - reset_thread_store 重置单例（测试隔离）
"""

# 元数据文件名
_THREAD_META_FILE = "meta.json"


@dataclass
class ThreadMeta:
    """一条线程的元数据。"""

    thread_id: str
    user_id: str
    title: str = "新会话"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    message_count: int = 0
    last_message_preview: str = ""
    status: str = "idle"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（落盘用）。"""
        # 直接把 dataclass 字段摊平成 dict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThreadMeta:
        """从字典还原（忽略未知/缺省字段，向前兼容）。

        参数：
            data: 落盘的字典数据

        返回：
            ThreadMeta 实例（只取已知字段，缺省走默认值）
        """
        # 只挑 dataclass 认识的键，未知键忽略，保证老数据可读
        return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})


class ThreadStore:
    """线程元数据仓库（进程内缓存 + JSON 原子落盘）。"""

    def __init__(self, *, force_user_dir: Path | None = None) -> None:
        """初始化；force_user_dir 指定用户目录根（默认用 harness paths）。

        参数：
            force_user_dir: 覆盖用户目录根（测试注入临时目录用）
        """
        # user_id -> {thread_id -> 元数据 dict} 的两级缓存
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._force_user_dir = force_user_dir
        self._lock = threading.RLock()


    def _user_dir(self, user_id: str) -> Path:
        """用户目录根。"""
        # 显式指定优先，否则走 harness 全局路径
        if self._force_user_dir is not None:
            return self._force_user_dir / user_id
        return get_paths().user_dir(user_id)

    def _users_dir(self, user_id: str) -> Path:
        """线程所在目录（{user_dir}/threads）。"""
        return self._user_dir(user_id) / "threads"

    def _meta_file(self, user_id: str) -> Path:
        """元数据文件路径。"""
        return self._users_dir(user_id) / _THREAD_META_FILE


    def _load(self, user_id: str) -> dict[str, dict[str, Any]]:
        """加载用户线程表（读磁盘，未缓存时）。"""
        # 命中缓存直接返回
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        meta_file = self._meta_file(user_id)
        data: dict[str, dict[str, Any]] = {}
        # 读文件：不存在/读失败都当空表（不抛）
        try:
            raw = meta_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        except OSError:
            pass
        else:
            # 解析 JSON，格式坏也当空表处理
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    threads = parsed.get("threads")
                    if isinstance(threads, dict):
                        data = {str(k): v for k, v in threads.items() if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError):
                pass
        self._cache[user_id] = data
        return data

    def _save(self, user_id: str, data: dict[str, dict[str, Any]]) -> None:
        """原子落盘用户线程表（临时文件 + os.replace）。"""
        users_dir = self._users_dir(user_id)
        users_dir.mkdir(parents=True, exist_ok=True)
        meta_file = self._meta_file(user_id)
        # 先写临时文件再原子替换，避免半截写入
        tmp_file = meta_file.with_suffix(".json.tmp")
        body = json.dumps({"threads": data}, ensure_ascii=False, indent=2)
        tmp_file.write_text(body, encoding="utf-8")
        os.replace(tmp_file, meta_file)

    def _update_locked(
        self,
        user_id: str,
        thread_id: str,
        *,
        fields: dict[str, Any] | None = None,
    ) -> ThreadMeta | None:
        """更新并落盘；返回更新后的元数据（不存在返回 None）。"""
        data = self._load(user_id)
        entry = data.get(thread_id)
        if entry is None:
            return None
        if fields:
            entry.update(fields)
        self._save(user_id, data)
        return ThreadMeta.from_dict(entry)


    def create(
        self,
        user_id: str,
        *,
        thread_id: str | None = None,
        title: str = "新会话",
    ) -> ThreadMeta:
        """创建线程元数据（不建磁盘目录）。

        参数：
            user_id: 用户
            thread_id: 指定线程 id；缺省用 uuid4().hex
            title: 初始标题

        返回：
            新建或已存在的 ThreadMeta（指定 thread_id 且已存在则幂等返回）
        """
        tid = thread_id or uuid.uuid4().hex
        _validate_thread_id(tid)
        with self._lock:
            data = self._load(user_id)
            # 已存在则幂等返回
            existing = data.get(tid)
            if existing is not None:
                return ThreadMeta.from_dict(existing)
            meta = ThreadMeta(thread_id=tid, user_id=user_id, title=title)
            data[tid] = meta.to_dict()
            self._save(user_id, data)
            return meta

    def get(self, user_id: str, thread_id: str) -> ThreadMeta | None:
        """查询线程元数据。

        参数：
            user_id: 用户
            thread_id: 线程 id

        返回：
            ThreadMeta；不存在返回 None
        """
        _validate_thread_id(thread_id)
        with self._lock:
            data = self._load(user_id)
            entry = data.get(thread_id)
            return ThreadMeta.from_dict(entry) if entry else None

    def list(self, user_id: str) -> list[ThreadMeta]:
        """列出用户全部线程，按 updated_at 倒序（最新在前）。"""
        with self._lock:
            data = self._load(user_id)
            metas = [ThreadMeta.from_dict(entry) for entry in data.values()]
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas

    def rename(self, user_id: str, thread_id: str, new_title: str) -> ThreadMeta:
        """重命名线程。

        参数：
            user_id: 用户
            thread_id: 线程 id
            new_title: 新标题（非空）

        返回：
            更新后的 ThreadMeta

        异常：
            标题为空抛 ValueError；线程不存在抛 KeyError
        """
        _validate_thread_id(thread_id)
        title = (new_title or "").strip()
        # 标题不允许为空
        if not title:
            raise ValueError("标题不能为空")
        with self._lock:
            updated = self._update_locked(
                user_id,
                thread_id,
                fields={"title": title, "updated_at": time.time()},
            )
            if updated is None:
                raise KeyError(f"线程不存在: {thread_id}")
            return updated

    def update(
        self,
        user_id: str,
        thread_id: str,
        *,
        message_count: int | None = None,
        last_message_preview: str | None = None,
        status: str | None = None,
        title: str | None = None,
    ) -> ThreadMeta | None:
        """通用更新（run 结束回填消息数/预览/状态等）。

        参数：
            user_id: 用户
            thread_id: 线程 id
            message_count / last_message_preview / status / title: 需更新的字段，
                None 表示不改动该项

        返回：
            更新后的 ThreadMeta；线程不存在返回 None
        """
        _validate_thread_id(thread_id)
        # 只把显式传入（非 None）的字段纳入更新
        fields: dict[str, Any] = {}
        if message_count is not None:
            fields["message_count"] = message_count
        if last_message_preview is not None:
            fields["last_message_preview"] = last_message_preview
        if status is not None:
            fields["status"] = status
        if title is not None:
            fields["title"] = title
        fields["updated_at"] = time.time()
        with self._lock:
            return self._update_locked(user_id, thread_id, fields=fields)

    def exists(self, user_id: str, thread_id: str) -> bool:
        """线程是否已登记。"""
        return self.get(user_id, thread_id) is not None

    async def delete(self, user_id: str, thread_id: str) -> bool:
        """删除线程：先异步删 user-data 目录（失败中止保留 meta），再删元数据。

        参数：
            user_id: 用户
            thread_id: 线程 id

        返回：
            删除成功返回 True；线程本就不存在返回 False
        """
        _validate_thread_id(thread_id)
        with self._lock:
            data = self._load(user_id)
            if thread_id not in data:
                return False
        # 目录删除失败直接向上抛，元数据保留（保证「有 meta 必有目录」）
        try:
            await asyncio.to_thread(
                self._remove_thread_dir,
                user_id,
                thread_id,
            )
        except OSError:
            raise
        # 目录删除成功后再摘元数据
        with self._lock:
            data = self._load(user_id)
            if thread_id in data:
                del data[thread_id]
                self._save(user_id, data)
        return True

    def _remove_thread_dir(self, user_id: str, thread_id: str) -> None:
        """线程池：调 harness 删除线程目录（含穿越安全检查）。"""
        get_paths().delete_thread_dir(thread_id, user_id=user_id)



_thread_stores: dict[str, ThreadStore] = {}
_thread_store_lock = threading.Lock()


def get_thread_store(*, force_user_dir: Path | None = None) -> ThreadStore:
    """返回进程级 ThreadStore 单例（懒构造，线程安全）。

    参数：
        force_user_dir: 指定用户目录根；按其作为单例键区分实例

    返回：
        对应键的 ThreadStore 单例
    """
    key = str(force_user_dir) if force_user_dir is not None else "default"
    with _thread_store_lock:
        store = _thread_stores.get(key)
        if store is not None:
            return store
        store = ThreadStore(force_user_dir=force_user_dir)
        _thread_stores[key] = store
        return store


def reset_thread_store() -> None:
    """清空单例缓存（测试隔离用）。"""
    global _thread_stores
    with _thread_store_lock:
        _thread_stores = {}
