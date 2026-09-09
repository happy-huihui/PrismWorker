"""会话状态 checkpoint 工厂（短期记忆 = 对话原文的持久化载体）。

用 LangGraph 官方 AsyncSqliteSaver 把每一轮 Human/AI/工具消息全量落进
checkpoints.db，按 thread_id 区分会话（换 thread_id = 新会话，同
thread_id 重连 = 恢复旧会话）。本模块只负责「工厂」：给出库文件位置、
建好连接与表结构，返回一个已就绪的 saver；生命周期关闭由调用方负责。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harness.config.memory_config import MemoryConfig

logger = logging.getLogger(__name__)


async def create_checkpointer(config: "MemoryConfig | None" = None) -> Any:
    """创建并初始化短期记忆 checkpointer（AsyncSqliteSaver）。

    用法：
        checkpointer = await create_checkpointer()
        # ... 装配到主图（阶段8 lead_agent）...
        await checkpointer.conn.close()   # 用完负责关闭底层连接

    Args:
        config: 记忆配置；缺省用全局配置。库文件位置由
            root_dir + checkpoints_db_file 决定。

    Returns:
        已就绪的 AsyncSqliteSaver 实例（未关闭，调用方负责 aclose）。
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from harness.memory.manager import resolve_memory_paths

    _, db_path = resolve_memory_paths(config)

    conn = await aiosqlite.connect(str(db_path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    logger.info("短期记忆 checkpoint 就绪: %s", db_path)
    return saver