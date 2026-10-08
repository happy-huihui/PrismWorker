from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harness.config.memory_config import MemoryConfig

logger = logging.getLogger(__name__)

"""短期记忆 checkpoint 工厂

    职责：用 LangGraph 官方 AsyncSqliteSaver 把每轮消息全量落进 checkpoints.db
        - 按 thread_id 区分会话（换 id = 新会话，同 id = 续会话）
        - 只做工厂：解析库位置、建连接与表；连接关闭由调用方负责

    对外暴露：
        - create_checkpointer
"""


async def create_checkpointer(config: "MemoryConfig | None" = None) -> Any:
    """创建并初始化短期记忆 checkpointer（AsyncSqliteSaver）。

    参数：
        config: 记忆配置；缺省用全局配置。库位置 = root_dir + checkpoints_db_file

    返回：
        已就绪的 AsyncSqliteSaver（未关闭，调用方负责 aclose / conn.close）

    用法：
        checkpointer = await create_checkpointer()
        ... 装配到主图 ...
        await checkpointer.conn.close()
    """
    # 延迟导入：aiosqlite / langgraph 是重依赖，放函数内避免拖慢模块导入
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # 直接走路径模块（真实家），不绕经 manager
    # 直接走路径模块取库位置（不绕 manager，避免反向依赖）
    from harness.memory.paths import resolve_memory_paths

    # 库位置 = root_dir + checkpoints_db_file
    _, db_path = resolve_memory_paths(config)

    # 建连接 + 建表（setup 幂等）
    # 建连接 + setup 建表（setup 幂等，可重复调用）
    conn = await aiosqlite.connect(str(db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    # 返回未关闭的 saver，连接生命周期交给调用方
    logger.info("短期记忆 checkpoint 就绪: %s", db_path)
    return saver
