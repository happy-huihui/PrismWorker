"""记忆后端私有配置（PrismMemConfig）。

Host 共享字段在 ``harness/config/memory_config.py`` 的 ``MemoryConfig`` 上，
本模块是「记忆后端」自己的全部调优参数，由 ``MemoryConfig.backend_config``
（dict）解析而来——与参考实现的后端私有配置职责一致：共享 schema 不
泄漏后端旋钮，后端不依赖 host 字段。

字段分组：
    - 存储：storage_path（空 = {数据根目录}/data，见 paths.py）
    - 队列：debounce_seconds / queue_max_depth（异步防抖与背压）
    - 事实：max_facts / fact_confidence_threshold（容量与置信度门槛）
    - 注入：max_injection_tokens / guaranteed_categories /
      guaranteed_token_budget（注入预算与必保类别）
    - 增量：watermark_max_keys（水位线 LRU 上限）
    - 模板：patterns_dir / prompts_dir（覆盖内置信号模式与提示词）
    - 模型：model（记忆提取模型名；None = 复用主模型）
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class PrismMemConfig(BaseModel):
    """记忆后端私有配置（PrismMem）。"""

    # ── 存储 ──────────────────────────────────────────────────────────
    storage_path: str = Field(
        default="",
        description="记忆数据根目录。空 = {数据根目录}/data；"
        "每用户 memory.json 落在 {storage_path}/users/{user_id}/memory.json",
    )

    # ── 队列（异步 + 防抖 + 背压）──────────────────────────────────────
    debounce_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        description="防抖窗口：多少秒内同 (thread, user) 的多次更新合并为一次提取",
    )
    queue_max_depth: int = Field(
        default=1000,
        ge=0,
        description="队列背压上限；0=不限。满时拒绝非信号更新（信号更新永远准入）",
    )

    # ── 事实（容量与置信度）────────────────────────────────────────────
    max_facts: int = Field(
        default=100,
        ge=10,
        le=500,
        description="事实数量上限，超限按置信度升序裁剪",
    )
    fact_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="事实落库最小置信度（低于门槛丢弃）",
    )

    # ── 注入（预算与必保类别）──────────────────────────────────────────
    max_injection_tokens: int = Field(
        default=2000,
        ge=100,
        le=8000,
        description="记忆注入文本的最大 token 预算（字符估算）",
    )
    guaranteed_categories: list[str] = Field(
        default_factory=lambda: ["correction"],
        description="无论预算如何都必须注入的事实类别（如纠错类）",
    )
    guaranteed_token_budget: int = Field(
        default=500,
        ge=50,
        le=2000,
        description="必保类别事实的 token 上限",
    )

    # ── 增量更新（水位线缓存）──────────────────────────────────────────
    watermark_max_keys: int = Field(
        default=4096,
        ge=0,
        description="内存水位线 LRU 上限；0=不限。条目被逐出时该线程下轮重提一批（同重启）",
    )

    # ── 模板覆盖（外部化信号模式与提示词）──────────────────────────────
    patterns_dir: str | None = Field(
        default=None,
        description="信号模式目录（correction.yaml 等 7 个文件）；None=内置 pattern/",
    )
    prompts_dir: str | None = Field(
        default=None,
        description="提示词模板目录；None=内置 prompts/",
    )

    # ── 记忆提取模型 ───────────────────────────────────────────────────
    model: str | None = Field(
        default=None,
        description="记忆提取模型名（与主模型同一配置表）；None=复用当前激活模型",
    )

    @model_validator(mode="after")
    def _validate_storage_path(self) -> PrismMemConfig:
        """storage_path 是根目录而非文件（与参考实现一致，防手误把文件路径填进来）。"""
        if self.storage_path:
            from pathlib import Path

            resolved = Path(self.storage_path)
            if resolved.is_file():
                raise ValueError(
                    f"memory.backend_config.storage_path={self.storage_path!r} 指向一个文件；"
                    "PrismMem 把它当作根目录（每用户 memory.json 在 {storage_path}/users/{uid}/memory.json），"
                    "请改指一个目录"
                )
        return self

    @classmethod
    def from_backend_config(cls, backend_config: dict[str, Any] | None) -> PrismMemConfig:
        """从 MemoryConfig.backend_config 解析；未知 key 告警（防拼写错误静默回退）。"""
        if not backend_config:
            return cls()
        config_dict = dict(backend_config)
        known = {k: v for k, v in config_dict.items() if k in cls.model_fields and v is not None}
        unknown = sorted(k for k in config_dict if k not in cls.model_fields)
        if unknown:
            logger.warning(
                "记忆后端配置包含未知 key（检查拼写，可能被静默忽略）: %s",
                unknown,
            )
        return cls(**known)