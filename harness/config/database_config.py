from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

"""数据库配置

    职责：定义 PostgreSQL 连接参数，供 checkpoint 持久化与记忆后端共用。

    对外暴露：
        - CheckpointChannelMode  checkpoint 通道模式（full=全量快照 / delta=增量）
        - DEFAULT_CHECKPOINT_SNAPSHOT_FREQUENCY  增量模式下每几步落一次全量快照
        - DatabaseConfig         PostgreSQL 连接配置
"""

CheckpointChannelMode = Literal["full", "delta"]

# delta 模式下的全量快照间隔（步）
DEFAULT_CHECKPOINT_SNAPSHOT_FREQUENCY = 10


class DatabaseConfig(BaseModel):
    """PostgreSQL 数据库配置（checkpoint 与记忆共用）。"""

    postgres_url: str = Field(
        default="",
        description="PostgreSQL 连接串（支持 $DATABASE_URL 环境变量引用）",
    )

    postgres_schema: str = Field(default="", description="PostgreSQL schema")

    pool_size: int = Field(default=5, ge=1, description="连接池大小")

    pool_recycle: int = Field(default=300, gt=0, description="连接回收间隔(秒)")

    command_timeout: float | None = Field(
        default=30, description="数据库命令超时(秒，null 不设)"
    )

    echo_sql: bool = Field(default=False, description="打印 SQL 日志")

    def resolved_url(self) -> str:
        """返回实际连接串：环境变量 DATABASE_URL 优先，其次配置值。"""
        # 环境变量优先，便于容器/CI 覆盖
        env_url = os.getenv("DATABASE_URL")
        if env_url:
            return env_url
        return self.postgres_url or ""

    @property
    def is_configured(self) -> bool:
        """是否已配置可用连接串。"""
        return bool(self.resolved_url())
