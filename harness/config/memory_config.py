"""记忆配置（SQLite 文件持久化）。

管理 Agent 的持久记忆（按用户确认改为本地 SQLite，不再依赖 PostgreSQL）：
    - 短期记忆（会话对话原文）→ checkpoints.db，由 LangGraph 官方
      AsyncSqliteSaver 承载，本配置只提供库文件位置；
    - 长期记忆（模型主动读写）→ memory.db，本模块自定义表结构。
两个库都放在 root_dir 下；root_dir 缺省为 {数据根目录}/data。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """记忆配置。"""

    enabled: bool = Field(default=True, description="是否启用记忆")

    mode: Literal["middleware", "tool"] = Field(
        default="tool", description="记忆注入方式"
    )

    backend: Literal["sqlite"] = Field(default="sqlite", description="记忆存储后端")

    root_dir: str | None = Field(default=None, description="记忆数据根目录（None=默认）")

    memory_db_file: str = Field(default="memory.db", description="长期记忆库文件名")

    checkpoints_db_file: str = Field(default="checkpoints.db", description="短期记忆 checkpoint 库文件名")

    table_name: str = Field(default="memory_entries", description="长期记忆表名")

    max_results: int = Field(default=5, description="记忆检索条数上限")

    max_memory_chars: int = Field(default=2000, description="单条记忆最大字符数")