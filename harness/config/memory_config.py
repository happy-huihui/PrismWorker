"""记忆配置（host 共享字段）。

本模块只放「所有记忆调用方共同读取」的共享字段，记忆后端的私有调优
参数一律放在 ``backend_config``（dict）里，由 ``harness/memory/config.py``
的 ``PrismMemConfig`` 自行解析，避免私有旋钮泄漏到共享 schema 上。

字段说明：
    - enabled: 总开关（false 时所有记忆链路直接短路）
    - mode: middleware = 中间件自动提取 + 注入；tool = 模型工具读写
    - injection_enabled: 是否把记忆文本注入系统提示
    - root_dir: 记忆数据根目录（None → {数据根目录}/data）
    - checkpoints_db_file: 短期记忆 checkpoints.db 文件名
    - max_results: 工具检索条数上限
    - backend_config: 透传给记忆后端的私有参数字典

兼容说明：backend / memory_db_file / table_name / max_memory_chars 是
已弃用字段，仅保留以兼容旧 config.yaml 与测试脚本的显式构造；新逻辑
不再读取它们（长期记忆已改为 memory.json 文档存储），出现非空值时
记 WARNING 提醒迁移。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class MemoryConfig(BaseModel):
    """记忆配置（host 共享字段）。"""

    enabled: bool = Field(default=True, description="是否启用记忆")

    mode: Literal["middleware", "tool"] = Field(
        default="middleware",
        description="记忆运行模式：middleware=中间件自动提取+注入；tool=模型工具读写",
    )

    injection_enabled: bool = Field(
        default=True,
        description="是否把长期记忆注入系统提示词",
    )

    root_dir: str | None = Field(default=None, description="记忆数据根目录（None=默认）")

    checkpoints_db_file: str = Field(
        default="checkpoints.db",
        description="短期记忆 checkpoint 库文件名",
    )

    max_results: int = Field(default=5, description="记忆检索条数上限")

    backend_config: dict[str, Any] = Field(
        default_factory=dict,
        description="记忆后端私有配置（由 PrismMemConfig 解析，见 harness/memory/config.py）",
    )

    # ── 已弃用兼容字段（不再生效，仅兼容旧配置/旧构造参数）──────────────
    backend: Literal["sqlite"] | None = Field(
        default=None, description="【已弃用】旧后端名；长期记忆已改为 memory.json，不再生效"
    )
    memory_db_file: str | None = Field(
        default=None, description="【已弃用】旧长期记忆库文件名；不再生效"
    )
    table_name: str | None = Field(
        default=None, description="【已弃用】旧记忆表名；不再生效"
    )
    max_memory_chars: int | None = Field(
        default=None, description="【已弃用】旧单条记忆字长上限；不再生效"
    )

    @model_validator(mode="after")
    def _warn_deprecated(self) -> MemoryConfig:
        """旧字段非空时告警，帮助存量配置迁移（不阻断启动）。"""
        for name in ("backend", "memory_db_file", "table_name", "max_memory_chars"):
            value = getattr(self, name)
            if value not in (None, ""):
                logger.warning(
                    "memory.%s=%r 已弃用（长期记忆改为 memory.json 文档存储），"
                    "该配置不再生效，请从 config.yaml 移除；"
                    "私有调优参数请放 memory.backend_config 下",
                    name,
                    value,
                )
        return self


def get_memory_config() -> MemoryConfig:
    """返回当前全局记忆配置（懒加载）。"""
    global _memory_config
    if _memory_config is None:
        from harness.config.app_config import get_app_config

        _memory_config = get_app_config().memory
    return _memory_config


def set_memory_config(config: MemoryConfig) -> None:
    """设置全局记忆配置（测试用）。"""
    global _memory_config
    _memory_config = config


_memory_config: MemoryConfig | None = None