from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from harness.config.paths import get_paths

logger = logging.getLogger(__name__)

"""用户自定义指令仓库

    职责：按用户维护一份自定义指令 agent.md，支持读 / 写 / 清空（缺失或空视为未设置）
        - 落盘 users/{uid}/agent.md，纯文本原子改写
        - 长度上限 MAX_AGENT_MD_LENGTH，mtime 缓存避免重复读盘

    对外暴露：
        - MAX_AGENT_MD_LENGTH  指令长度上限（字符数）
        - AgentMdStore         仓库本体（load / save / clear）
        - get_agent_md_store   进程级单例（懒构造、线程安全）
        - reset_agent_md_store 重置单例（测试隔离）
"""

# 指令长度上限（字符数）：个人偏好保持精简，防提示词膨胀
MAX_AGENT_MD_LENGTH = 5_000

# 固定文件名
_AGENT_MD_FILE = "agent.md"


class AgentMdStore:
    """用户自定义指令仓库（mtime 缓存 + JSON 无关的纯文本原子落盘）。"""

    def __init__(self, *, force_users_dir: Path | None = None) -> None:
        """初始化；force_users_dir 指定用户目录根（默认用 harness paths）。

        参数：
            force_users_dir: 覆盖用户目录根（测试注入临时目录用）
        """
        self._force_users_dir = force_users_dir
        self._lock = threading.RLock()
        # path -> (mtime, content) 的读缓存
        self._cache: dict[Path, tuple[float, str]] = {}

    def _md_file(self, user_id: str) -> Path:
        """指令文件路径（{user_dir}/agent.md）。"""
        # 显式指定优先，否则走 harness 全局路径
        if self._force_users_dir is not None:
            return self._force_users_dir / user_id / _AGENT_MD_FILE
        return get_paths().user_dir(user_id) / _AGENT_MD_FILE

    def file_path(self, user_id: str) -> Path:
        """指令文件路径（{user_dir}/agent.md，公开给 API 层查 mtime 用）。"""
        return self._md_file(user_id)

    def load(self, user_id: str) -> str:
        """读取用户自定义指令。

        参数：
            user_id: 用户

        返回：
            指令文本；未设置/读失败返回空串（不抛）
        """
        md_file = self._md_file(user_id)
        with self._lock:
            # 命中缓存且 mtime 未变，直接返回
            try:
                mtime = md_file.stat().st_mtime
            except OSError:
                mtime = None
            if mtime is not None:
                cached = self._cache.get(md_file)
                if cached is not None and cached[0] == mtime:
                    return cached[1]
            # 读文件：不存在/读失败都当未设置（不抛）
            try:
                content = md_file.read_text(encoding="utf-8")
            except FileNotFoundError:
                content = ""
            except OSError as exc:
                logger.warning("读取自定义指令失败（user=%s）: %s", user_id, exc)
                content = ""
            if mtime is not None:
                self._cache[md_file] = (mtime, content)
            return content

    def save(self, user_id: str, content: str) -> None:
        """保存用户自定义指令（strip 后为空则等价清空）。

        参数：
            user_id: 用户
            content: 指令文本；超上限抛 ValueError

        异常：
            内容超 MAX_AGENT_MD_LENGTH 抛 ValueError
        """
        text = (content or "").strip()
        if len(text) > MAX_AGENT_MD_LENGTH:
            raise ValueError(f"自定义指令超出长度上限 {MAX_AGENT_MD_LENGTH} 字符")
        md_file = self._md_file(user_id)
        with self._lock:
            md_file.parent.mkdir(parents=True, exist_ok=True)
            # 空内容直接清文件，避免空文件残留
            if not text:
                self.clear(user_id)
                return
            # 先写临时文件再原子替换，避免半截写入
            tmp_file = md_file.with_suffix(".md.tmp")
            tmp_file.write_text(text, encoding="utf-8")
            os.replace(tmp_file, md_file)
            # 写后立刻刷新缓存（stat 的 mtime 粒度足够，替换后必变）
            self._cache.pop(md_file, None)

    def clear(self, user_id: str) -> None:
        """清空用户自定义指令（文件缺失视为已清空，幂等）。"""
        md_file = self._md_file(user_id)
        with self._lock:
            self._cache.pop(md_file, None)
            try:
                md_file.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("清空自定义指令失败（user=%s）: %s", user_id, exc)


_agent_md_stores: dict[str, AgentMdStore] = {}
_agent_md_store_lock = threading.Lock()


def get_agent_md_store(*, force_users_dir: Path | None = None) -> AgentMdStore:
    """返回进程级 AgentMdStore 单例（懒构造，线程安全）。

    参数：
        force_users_dir: 指定用户目录根；按其作为单例键区分实例

    返回：
        对应键的 AgentMdStore 单例
    """
    key = str(force_users_dir) if force_users_dir is not None else "default"
    with _agent_md_store_lock:
        store = _agent_md_stores.get(key)
        if store is not None:
            return store
        store = AgentMdStore(force_users_dir=force_users_dir)
        _agent_md_stores[key] = store
        return store


def reset_agent_md_store() -> None:
    """清空单例缓存（测试隔离用）。"""
    global _agent_md_stores
    with _agent_md_store_lock:
        _agent_md_stores = {}
