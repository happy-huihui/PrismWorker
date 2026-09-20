from __future__ import annotations

import logging
import threading

from harness.memory.config import PrismMemConfig
from harness.memory.manager.contract import MemoryManager
from harness.memory.manager.prism import PrismMemoryManager
from harness.memory.storage import migrate_legacy_sqlite

logger = logging.getLogger(__name__)

"""记忆管理器单例工厂（manager.factory）

    职责：按全局配置懒构建进程级唯一 MemoryManager，并在线程安全下复用；
         首次构建时顺带执行一次「旧 SQLite 库迁移」。
    解耦：从 harness.config.memory_config 取 host 配置，转成后端私有
         PrismMemConfig，再装配 PrismMemoryManager。
"""

# 单例与其构造锁
_manager: MemoryManager | None = None
_manager_lock = threading.Lock()


def get_memory_manager() -> MemoryManager:
    """返回进程级记忆管理器单例（线程安全；首次构建时顺带执行旧库迁移）。

    返回：
        MemoryManager 实例。记忆是否启用由调用方（中间件 / 工具）自行判断，
        本工厂始终可构建，以保持调用方简单。
    """
    global _manager
    # 1.快路径：已构建直接复用
    if _manager is not None:
        return _manager
    with _manager_lock:
        # 2.双重检查，避免并发重复构建
        if _manager is not None:
            return _manager

        # 3.取 host 配置 → 转后端私有配置
        from harness.config.memory_config import get_memory_config

        host_config = get_memory_config()
        mem_config = PrismMemConfig.from_backend_config(host_config.backend_config)
        # 4.旧 SQLite 库一次性迁移（幂等：无旧库 / 已备份则跳过；失败不阻断）
        try:
            migrated = migrate_legacy_sqlite(mem_config)
            if migrated:
                logger.info("旧长期记忆库迁移完成：%d 个用户文档", migrated)
        except Exception as exc:  # noqa: BLE001 —— 迁移失败不阻断记忆启动
            logger.warning("旧长期记忆库迁移失败（已跳过，不影响新记忆）: %s", exc)

        # 5.装配实现并缓存
        _manager = PrismMemoryManager(mem_config, mode=host_config.mode)
        logger.info(
            "记忆管理器就绪: mode=%s storage_path=%s",
            host_config.mode,
            mem_config.storage_path or "(默认数据目录)",
        )
        return _manager


def reset_memory_manager() -> None:
    """清空单例（测试 / 运行时重建用），并关闭旧实例。"""
    global _manager
    with _manager_lock:
        current = _manager
        _manager = None
        # 关闭被替换掉的实例（异常忽略，不影响重置）
        if current is not None:
            try:
                current.close()
            except Exception:  # noqa: BLE001
                logger.debug("记忆管理器关闭异常（忽略）", exc_info=True)
