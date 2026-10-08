from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING

"""记忆路径与身份解析

    职责：把用户身份与配置解析成记忆文件的真实磁盘路径
        - safe_user_id 身份规范化（非法字符替换 + 短摘要防碰撞）
        - memory_root / memory_file_path 记忆根与每用户 memory.json
        - resolve_memory_paths 兼容旧接口的 (记忆根, checkpoints.db)

    对外暴露：
        - safe_user_id / validate_agent_name
        - memory_root / memory_file_path
        - resolve_memory_paths / AGENT_NAME_PATTERN / DEFAULT_AGENT_BUCKET
"""

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
    # 先把所有非法字符统一替换成短横线
    sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw)
    # 本来就在安全字符集内：原样返回，保证幂等（再调一次结果不变）
    if sanitized == raw:
        return raw
    # 有替换时追加原始值的短摘要，避免不同原始 id 归一后撞名
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[: _SAFE_USER_ID_DIGEST_HEX_LEN]
    return f"{sanitized}-{digest}"


def validate_agent_name(name: str) -> None:
    """校验 agent_name 合法性（防止目录穿越）。"""
    # 空名直接拒（否则会拼出空目录名）
    if not name:
        raise ValueError("agent_name 必须是非空字符串")
    # 内部保留桶放行；其余只允许字母数字与短横线（防目录穿越）
    if name != DEFAULT_AGENT_BUCKET and AGENT_NAME_PATTERN.match(name) is None:
        raise ValueError(
            f"非法的 agent_name {name!r}: 只允许字母数字与短横线"
        )


def _data_root() -> Path:
    """全局数据根目录（与 Paths.base_dir 同源，避免循环导入时手工解析）。"""
    # 延迟导入：避免 config.paths ↔ memory.paths 循环导入
    from harness.config.paths import get_paths

    return get_paths().base_dir / "data"


def memory_root(mem_config: PrismMemConfig) -> Path:
    """记忆数据根目录：storage_path 优先，空则回退 {数据根目录}/data。"""
    # 配了 storage_path 就用它，否则回退到全局数据根（与 checkpoints.db 同盘）
    if mem_config.storage_path:
        return Path(mem_config.storage_path).resolve()
    return _data_root()


def memory_file_path(mem_config: PrismMemConfig, user_id: str) -> Path:
    """返回某用户的 memory.json 文档路径（不创建）。"""
    # 每用户一份文档；id 先做安全归一
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
    # 未传配置时取全局记忆配置
    if config is None:
        from harness.config.app_config import get_app_config

        config = get_app_config().memory
    # root_dir 优先，空则用默认数据根
    if config.root_dir:
        root = Path(config.root_dir).resolve()
    else:
        root = _data_root()
    # 父目录不存在就建：调用方要拿它去连 checkpoints.db
    root.mkdir(parents=True, exist_ok=True)
    return root, root / config.checkpoints_db_file
