"""PrismWorker 路径管理模块。

提供沙箱虚拟路径 <==> 宿主机真实路径 的双向映射，集中管理所有文件系统路径约定。
"""

from __future__ import annotations

import os
import re
from pathlib import Path


VIRTUAL_PATH_PREFIX = "/mnt/user-data"


_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_thread_id(thread_id: str) -> str:
    """校验 thread_id 合法性，防止路径穿越攻击。

    要求：
    - 必须是字符串
    - 长度 1-64
    - 只包含 ASCII 字母、数字、下划线、短横线
    """
    if not isinstance(thread_id, str) or _THREAD_ID_RE.fullmatch(thread_id) is None:
        raise ValueError(
            "Invalid thread_id: expected 1-64 ASCII letters, digits, hyphens,"
            f" or underscores, got {thread_id!r}"
        )
    return thread_id


def _validate_user_id(user_id: str) -> str:
    """校验 user_id 合法性。

    要求：至少 1 个字符，只包含 ASCII 字母、数字、下划线、短横线。
    """
    if not _SAFE_USER_ID_RE.match(user_id):
        raise ValueError(
            f"Invalid user_id {user_id!r}: only alphanumeric characters,"
            f" hyphens, and underscores are allowed."
        )
    return user_id



def _default_local_base_dir() -> Path:
    """返回默认的数据根目录。

    优先级：
    1. 环境变量 PRISM_WORKER_HOME → 直接使用
    2. 否则 → {当前工作目录}/.prism-worker
    """
    env_home = os.getenv("PRISM_WORKER_HOME")
    if env_home:
        return Path(env_home).resolve()
    return Path.cwd().resolve() / ".prism-worker"



class Paths:
    """PrismWorker 路径管理器。"""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """初始化路径管理器。

        base_dir: 数据根目录。未指定时使用环境变量 PRISM_WORKER_HOME，
                  或回退到 {cwd}/.prism-worker。
        """
        self._base_dir = (
            Path(base_dir).resolve()
            if base_dir is not None
            else _default_local_base_dir()
        )


    @property
    def base_dir(self) -> Path:
        """数据根目录。所有线程数据、用户数据均存储在此目录下。"""
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: str | Path) -> None:
        self._base_dir = Path(value).resolve()

    def user_dir(self, user_id: str) -> Path:
        """返回用户专属目录（不创建）。

        → {base_dir}/users/{user_id}
        """
        _validate_user_id(user_id)
        return self.base_dir / "users" / user_id

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """返回线程根目录（不创建）。

        - 传 user_id → {base_dir}/users/{user_id}/threads/{thread_id}  （多用户隔离）
        - 不传       → {base_dir}/threads/{thread_id}               （简单模式）
        """
        _validate_thread_id(thread_id)
        if user_id is not None:
            return self.user_dir(user_id) / "threads" / thread_id
        return self.base_dir / "threads" / thread_id


    def sandbox_user_data_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """线程 user-data 根目录。沙箱挂载的宿主机侧根目录。

        → {thread_dir}/user-data
        沙箱内对应：/mnt/user-data
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data"

    def sandbox_work_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """Agent 工作区目录。

        → {user-data}/workspace
        沙箱内对应：/mnt/user-data/workspace
        """
        return self.sandbox_user_data_dir(thread_id, user_id=user_id) / "workspace"

    def sandbox_uploads_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """用户上传文件目录。

        → {user-data}/uploads
        沙箱内对应：/mnt/user-data/uploads
        """
        return self.sandbox_user_data_dir(thread_id, user_id=user_id) / "uploads"

    def sandbox_outputs_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """Agent 产出文件目录。present_file 工具要求文件必须在此目录下。

        → {user-data}/outputs
        沙箱内对应：/mnt/user-data/outputs
        """
        return self.sandbox_user_data_dir(thread_id, user_id=user_id) / "outputs"


    def ensure_thread_dirs(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """创建线程所需的所有子目录，返回 user-data 根。

        线程初始化时调用一次，确保 workspace、uploads、outputs 目录存在。
        """
        root = self.sandbox_user_data_dir(thread_id, user_id=user_id)
        (root / "workspace").mkdir(parents=True, exist_ok=True)
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "outputs").mkdir(parents=True, exist_ok=True)
        return root

    def delete_thread_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path | None:
        """递归删除整个线程目录。内置安全检查，防止误删。

        只有确认目录位于 {base_dir}/threads/ 或 {base_dir}/users/*/threads/
        子树之下时才会执行删除。
        """
        import shutil

        tdir = self.thread_dir(thread_id, user_id=user_id).resolve()
        if "threads" not in [p.name for p in tdir.parents]:
            return None
        shutil.rmtree(tdir, ignore_errors=True)
        return tdir

    def resolve_virtual_path(
        self,
        thread_id: str,
        virtual_path: str,
        *,
        user_id: str | None = None,
    ) -> Path:
        """将沙箱虚拟路径解析为宿主机真实路径。

        例：/mnt/user-data/outputs/report.pdf
          → E:/.../base_dir/threads/abc123/user-data/outputs/report.pdf

        安全性：双重校验——前缀匹配 + 路径穿越检测。
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        relative = stripped[len(prefix):].lstrip("/")

        base = self.sandbox_user_data_dir(thread_id, user_id=user_id).resolve()
        actual = (base / relative).resolve()

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("Access denied: path traversal detected")

        return actual



_paths: Paths | None = None

def get_paths() -> Paths:
    """返回 Paths 的全局唯一实例（懒加载）。"""
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths