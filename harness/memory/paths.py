"""记忆存储路径解析（与参考实现的路径职责对应）。

- ``safe_user_id``：把外部身份规范化为文件系统安全字符集（保持幂等，丢消息
  用短 SHA-256 摘要防碰撞）；
- ``memory_root``：记忆数据根目录 = 后端 storage_path，空则回退
  ``{数据根目录}/data``（与全局 Paths 同源，保证与 checkpoints.db 同盘）；
- ``memory_file_path``：每用户一份 memory.json 文档（{root}/users/{uid}/memory.json）；
- ``resolve_memory_paths``：兼容旧接口的二元组（记忆根目录, checkpoints.db 路径），
  供 checkpointer 与 app 层取 checkpoint 库位置继续使用。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.config.memory_config import MemoryConfig
    from harness.memory.config import PrismMemConfig

_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_UNSAFE_USER_ID_CHAR_RE = re.compile(r"[^A-Za-z0-9_\-]")
_SAFE_USER_ID_DIGEST_HEX_LEN = 16

# agent_name 规范化：本项目为单 Lead Agent 架构，接受与否只做安全校验，
# 存储一律按 user 分桶（见 storage.py 文档说明）。
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
# 缺省 agent 桶（与参考实现的内部桶保持一致，保持对外签名兼容）。
DEFAULT_AGENT_BUCKET = "__default__"


def safe_user_id(raw: str) -> str:
    """把任意外部身份规范化到 ``[A-Za-z0-9_-]`` 字符集（幂等）。"""
    if not raw or not isinstance(raw, str):
        raise ValueError("user_id 必须是非空字符串")
    sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw)
    if sanitized == raw:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[: _SAFE_USER_ID_DIGEST_HEX_LEN]
    return f"{sanitized}-{digest}"


def validate_agent_name(name: str) -> None:
    """校验 agent_name 合法性（防止目录穿越）。"""
    if not name:
        raise ValueError("agent_name 必须是非空字符串")
    if name != DEFAULT_AGENT_BUCKET and AGENT_NAME_PATTERN.match(name) is None:
        raise ValueError(
            f"非法的 agent_name {name!r}: 只允许字母数字与短横线"
        )


def _data_root() -> Path:
    """全局数据根目录（与 Paths.base_dir 同源，避免循环导入时手工解析）。"""
    from harness.config.paths import get_paths

    return get_paths().base_dir / "data"


def memory_root(mem_config: PrismMemConfig) -> Path:
    """记忆数据根目录：storage_path 优先，空则回退 {数据根目录}/data。"""
    if mem_config.storage_path:
        return Path(mem_config.storage_path).resolve()
    return _data_root()


def memory_file_path(mem_config: PrismMemConfig, user_id: str) -> Path:
    """返回某用户的 memory.json 文档路径（不创建）。"""
    root = memory_root(mem_config)
    return root / "users" / safe_user_id(user_id) / "memory.json"


def resolve_memory_paths(config: MemoryConfig | None = None) -> tuple[Path, Path]:
    """解析记忆相关两个库的完整路径（兼容旧接口）。

    Args:
        config: 记忆配置；缺省用全局配置。

    Returns:
        (记忆根目录, checkpoints.db 路径)；调用方通常只用第二个取 checkpoint
        库位置；父目录不存在时自动创建。
    """
    if config is None:
        from harness.config.app_config import get_app_config

        config = get_app_config().memory
    if config.root_dir:
        root = Path(config.root_dir).resolve()
    else:
        root = _data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root, root / config.checkpoints_db_file