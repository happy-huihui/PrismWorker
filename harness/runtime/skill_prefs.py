from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable

from harness.config.paths import get_paths

logger = logging.getLogger(__name__)

"""用户技能黑名单仓库（skill_prefs）

    职责：管理每用户一份的技能黑名单——被用户在设置里关闭的技能 name 集合。
    存储：{base_dir}/users/{user_id}/skill_blacklist.json，结构
         {"blocked": ["deep-research", ...]}，原子改写（临时文件 + os.replace）。
         文件缺失/解析失败都视为「空黑名单」（全部技能可用）。
    用途：API 路由（app/api/routes/skills.py）负责读写；
         SkillActivationMiddleware 每轮模型调用按 runtime 用户加载，
         在装配技能清单/响应激活前过滤掉黑名单内的技能。
    缓存：mtime 缓存——文件未变时直接返回缓存集合，避免每轮模型调用重复读盘。

    对外暴露：
        - SkillBlacklistStore        仓库本体（load/save）
        - get_skill_blacklist_store  进程级单例
        - reset_skill_blacklist_store 重置单例（测试隔离）
"""

# 固定文件名
_BLACKLIST_FILE = "skill_blacklist.json"


class SkillBlacklistStore:
    """用户技能黑名单仓库（mtime 缓存 + JSON 原子落盘）。"""

    def __init__(self, *, force_users_dir: Path | None = None) -> None:
        """初始化；force_users_dir 指定用户目录根（默认用 harness paths）。

        参数：
            force_users_dir: 覆盖用户目录根（测试注入临时目录用）
        """
        self._force_users_dir = force_users_dir
        self._lock = threading.RLock()
        # path -> (mtime, blocked set) 的读缓存
        self._cache: dict[Path, tuple[float, frozenset[str]]] = {}

    def _blacklist_file(self, user_id: str) -> Path:
        """黑名单文件路径（{user_dir}/skill_blacklist.json）。"""
        # 显式指定优先，否则走 harness 全局路径
        if self._force_users_dir is not None:
            return self._force_users_dir / user_id / _BLACKLIST_FILE
        return get_paths().user_dir(user_id) / _BLACKLIST_FILE

    def load(self, user_id: str) -> frozenset[str]:
        """读取用户技能黑名单。

        参数：
            user_id: 用户

        返回：
            被关闭的技能 name 集合；未设置/读失败返回空集（不抛）
        """
        blacklist_file = self._blacklist_file(user_id)
        with self._lock:
            # 命中缓存且 mtime 未变，直接返回
            try:
                mtime: float | None = blacklist_file.stat().st_mtime
            except OSError:
                mtime = None
            if mtime is not None:
                cached = self._cache.get(blacklist_file)
                if cached is not None and cached[0] == mtime:
                    return cached[1]
            # 读文件：不存在/读失败/格式坏都当空黑名单（不抛）
            blocked: frozenset[str] = frozenset()
            try:
                raw = blacklist_file.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    names = data.get("blocked")
                    if isinstance(names, list):
                        blocked = frozenset(str(n) for n in names if str(n).strip())
            except FileNotFoundError:
                pass
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("读取技能黑名单失败（user=%s）: %s", user_id, exc)
            if mtime is not None:
                self._cache[blacklist_file] = (mtime, blocked)
            return blocked

    def save(self, user_id: str, names: Iterable[str]) -> frozenset[str]:
        """保存用户技能黑名单（去重去空后全量覆盖写）。

        参数：
            user_id: 用户
            names: 被关闭的技能 name 列表；空列表等价清空黑名单

        返回：
            规整后落盘的黑名单集合
        """
        # 去重去空、排序，保证落盘内容稳定
        blocked = frozenset(str(n).strip() for n in names if str(n).strip())
        blacklist_file = self._blacklist_file(user_id)
        with self._lock:
            self._cache.pop(blacklist_file, None)
            # 空黑名单直接清文件，避免空文件残留
            if not blocked:
                self._clear(user_id)
                return blocked
            blacklist_file.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件再原子替换，避免半截写入
            tmp_file = blacklist_file.with_suffix(".json.tmp")
            body = json.dumps({"blocked": sorted(blocked)}, ensure_ascii=False, indent=2)
            tmp_file.write_text(body, encoding="utf-8")
            os.replace(tmp_file, blacklist_file)
            return blocked

    def _clear(self, user_id: str) -> None:
        """清空黑名单文件（文件缺失视为已清空，幂等）。"""
        blacklist_file = self._blacklist_file(user_id)
        try:
            blacklist_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("清空技能黑名单失败（user=%s）: %s", user_id, exc)


_skill_blacklist_stores: dict[str, SkillBlacklistStore] = {}
_skill_blacklist_store_lock = threading.Lock()


def get_skill_blacklist_store(*, force_users_dir: Path | None = None) -> SkillBlacklistStore:
    """返回进程级 SkillBlacklistStore 单例（懒构造，线程安全）。

    参数：
        force_users_dir: 指定用户目录根；按其作为单例键区分实例

    返回：
        对应键的 SkillBlacklistStore 单例
    """
    key = str(force_users_dir) if force_users_dir is not None else "default"
    with _skill_blacklist_store_lock:
        store = _skill_blacklist_stores.get(key)
        if store is not None:
            return store
        store = SkillBlacklistStore(force_users_dir=force_users_dir)
        _skill_blacklist_stores[key] = store
        return store


def reset_skill_blacklist_store() -> None:
    """清空单例缓存（测试隔离用）。"""
    global _skill_blacklist_stores
    with _skill_blacklist_store_lock:
        _skill_blacklist_stores = {}
