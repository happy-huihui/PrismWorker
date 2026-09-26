from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

"""路径管理：沙箱虚拟路径 <==> 宿主机真实路径

    职责：集中管理所有文件系统路径约定，并提供双向映射与路径安全校验。

    目录布局（base_dir = 数据根目录）：
        {base_dir}/users/{user_id}/threads/{thread_id}/user-data
            ├── workspace   工作区（沙箱内 /mnt/user-data/workspace）
            ├── uploads     用户上传（沙箱内 /mnt/user-data/uploads）
            └── outputs     Agent 产出（沙箱内 /mnt/user-data/outputs）

    对外暴露：
        - VIRTUAL_PATH_PREFIX     user-data 在沙箱内的挂载点
        - SKILLS_CONTAINER_PREFIX 技能目录在沙箱内的挂载点（只读、全线程共享）
        - PROJECT_ROOT            项目根（相对路径的解析基准）
        - Paths                   路径管理器
        - get_paths               Paths 全局单例
"""

VIRTUAL_PATH_PREFIX = "/mnt/user-data"

# 技能目录在容器内的挂载点（对齐 DeerFlow 的 DEFAULT_SKILLS_CONTAINER_PATH）。
# 与 VIRTUAL_PATH_PREFIX 的关键区别：这是**只读**挂载，且**全线程共享同一份**
# 宿主目录（bind mount，不复制数据），因此不参与 per-thread 隔离。
# 定在此处而非 SkillsConfig，是为了让沙箱层与技能层共用同一常量，避免漂移。
SKILLS_CONTAINER_PREFIX = "/mnt/skills"

# 项目根（代码仓库根，与 CWD / 数据根都无关）：由本模块位置反推 parents[2]。
# 用途：解析配置里的**相对**路径（技能根、自定义挂载源）。
# 不用 CWD 的原因：服务可能从任意目录启动，用 CWD 会让同一份配置解析出不同结果。
PROJECT_ROOT = Path(__file__).resolve().parents[2]


# thread_id 只允许 1-64 位 ASCII 字母/数字/下划线/短横线
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# user_id 只允许 ASCII 字母/数字/下划线/短横线
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_thread_id(thread_id: str) -> str:
    """校验 thread_id 合法，防止路径穿越。

    参数：
        thread_id: 待校验的线程标识

    返回：
        原值（合法时）

    异常：
        类型不对或不匹配白名单字符集时抛 ValueError
    """
    # 必须是字符串且字符集/长度都合规
    if not isinstance(thread_id, str) or _THREAD_ID_RE.fullmatch(thread_id) is None:
        raise ValueError(
            "Invalid thread_id: expected 1-64 ASCII letters, digits, hyphens,"
            f" or underscores, got {thread_id!r}"
        )
    return thread_id


def _validate_user_id(user_id: str) -> str:
    """校验 user_id 合法（至少 1 字符，仅 ASCII 字母/数字/下划线/短横线）。

    参数：
        user_id: 待校验的用户标识

    返回：
        原值（合法时）

    异常：
        不匹配白名单字符集时抛 ValueError
    """
    if _SAFE_USER_ID_RE.match(user_id):
        return user_id
    raise ValueError(
        f"Invalid user_id {user_id!r}: only alphanumeric characters,"
        f" hyphens, and underscores are allowed."
    )


def _default_local_base_dir() -> Path:
    """返回默认数据根目录。

    取法：环境变量 PRISM_WORKER_HOME 优先，否则 {当前工作目录}/.prism-worker。

    返回：
        绝对路径形式的数据根
    """
    # 环境变量优先（部署时可指向独立数据盘）
    env_home = os.getenv("PRISM_WORKER_HOME")
    if env_home:
        return Path(env_home).resolve()
    # 否则落到 CWD 下的隐藏目录
    return Path.cwd().resolve() / ".prism-worker"


class Paths:
    """PrismWorker 路径管理器。"""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        """初始化路径管理器。

        参数：
            base_dir: 数据根目录；未指定时取 PRISM_WORKER_HOME，
                再回退到 {cwd}/.prism-worker
        """
        self._base_dir = (
            Path(base_dir).resolve()
            if base_dir is not None
            else _default_local_base_dir()
        )

    @property
    def base_dir(self) -> Path:
        """数据根目录：所有线程数据、用户数据都存其下。"""
        return self._base_dir

    @base_dir.setter
    def base_dir(self, value: str | Path) -> None:
        self._base_dir = Path(value).resolve()

    def user_dir(self, user_id: str) -> Path:
        """返回用户专属目录（不创建）。

        返回：
            {base_dir}/users/{user_id}
        """
        # 先校验再拼路径，避免穿越
        _validate_user_id(user_id)
        return self.base_dir / "users" / user_id

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """返回线程根目录（不创建）。

        参数：
            thread_id: 线程标识
            user_id: 传入则按多用户隔离布局，不传则用简单布局

        返回：
            传 user_id → {base_dir}/users/{user_id}/threads/{thread_id}
            不传       → {base_dir}/threads/{thread_id}
        """
        _validate_thread_id(thread_id)
        # 有 user_id 走隔离布局
        if user_id is not None:
            return self.user_dir(user_id) / "threads" / thread_id
        # 无 user_id 走简单布局
        return self.base_dir / "threads" / thread_id

    def sandbox_user_data_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """线程 user-data 根目录（沙箱挂载的宿主侧根）。

        返回：
            {thread_dir}/user-data，沙箱内对应 /mnt/user-data
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data"

    def sandbox_work_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """Agent 工作区目录。

        返回：
            {user-data}/workspace，沙箱内对应 /mnt/user-data/workspace
        """
        return self.sandbox_user_data_dir(thread_id, user_id=user_id) / "workspace"

    def sandbox_uploads_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """用户上传文件目录。

        返回：
            {user-data}/uploads，沙箱内对应 /mnt/user-data/uploads
        """
        return self.sandbox_user_data_dir(thread_id, user_id=user_id) / "uploads"

    def sandbox_outputs_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """Agent 产出文件目录（present_file 工具要求文件必须在此目录下）。

        返回：
            {user-data}/outputs，沙箱内对应 /mnt/user-data/outputs
        """
        return self.sandbox_user_data_dir(thread_id, user_id=user_id) / "outputs"

    def ensure_thread_dirs(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path:
        """创建线程所需全部子目录，返回 user-data 根。

        参数：
            thread_id: 线程标识
            user_id: 用户标识（可选）

        返回：
            user-data 根目录路径
        """
        root = self.sandbox_user_data_dir(thread_id, user_id=user_id)
        # 三个子目录一次建齐（存在则跳过）
        (root / "workspace").mkdir(parents=True, exist_ok=True)
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "outputs").mkdir(parents=True, exist_ok=True)
        return root

    def delete_thread_dir(
        self, thread_id: str, *, user_id: str | None = None
    ) -> Path | None:
        """递归删除整个线程目录，内置安全检查防误删。

        参数：
            thread_id: 线程标识
            user_id: 用户标识（可选）

        返回：
            被删除的目录路径；安全检查未通过时返回 None
        """
        tdir = self.thread_dir(thread_id, user_id=user_id).resolve()
        # 只有父链里带 threads 目录才允许删，挡住 base_dir 被整体误删
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
        """把沙箱虚拟路径解析为宿主机真实路径。

        例：/mnt/user-data/outputs/report.pdf
          → {base_dir}/users/{user_id}/threads/{thread_id}/user-data/outputs/report.pdf

        参数：
            thread_id: 线程标识
            virtual_path: 沙箱内绝对路径
            user_id: 用户标识（可选）

        返回：
            宿主机上的真实绝对路径

        异常：
            前缀不匹配或检测到路径穿越时抛 ValueError
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        # 校验一：必须落在 user-data 前缀下
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        # 去掉前缀，得到相对路径
        relative = stripped[len(prefix):].lstrip("/")

        base = self.sandbox_user_data_dir(thread_id, user_id=user_id).resolve()
        actual = (base / relative).resolve()

        # 校验二：解析后仍须在 base 之内，挡住 ../ 穿越
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
