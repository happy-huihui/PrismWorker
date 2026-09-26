"""基于 Docker all-in-one-sandbox 的 Aio 沙箱实现。

通过 agent_sandbox SDK（Fern 生成的 HTTP 客户端）操作沙箱容器：
    - shell.exec_command  —— 在容器内执行 bash 命令
    - file.read_file/write_file —— 读 / 写容器内文件
    - file.glob_files / grep_files / list_path —— 查找与查看

关键设计：
    1. 文件操作路径分两类：
       - **可写工作区** `/mnt/user-data`（读写皆可，per-thread 隔离）
       - **只读挂载区** `/mnt/skills`（只能读；写入会被拒绝，防止模型改写技能包）
       两者都不允许 .. 穿越。
    2. 命令执行失败（非零退出码）不抛异常，而是把退出码和输出
       原样返回给模型，让模型自行修正（参考成熟工具行为）；
       只有「连不上沙箱/网络错误」才抛 SandboxConnectionError。
    3. 输出按配置截断，防止把模型上下文撑爆。
    4. executor 与 lifecycle 分离：本文件只负责「指挥容器干活」，
       容器启动/销毁由 lifecycle.py 负责。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx
from agent_sandbox import Sandbox as AioSandboxClient
from agent_sandbox.core.api_error import ApiError

from harness.config.paths import SKILLS_CONTAINER_PREFIX, VIRTUAL_PATH_PREFIX
from harness.sandbox.exceptions import (
    SandboxCommandError,
    SandboxConnectionError,
    SandboxFileError,
    SandboxPathError,
)

logger = logging.getLogger(__name__)

_DEFAULT_NO_CHANGE_TIMEOUT = 600

_shell_lock = threading.Lock()


@dataclass
class GrepMatch:
    """grep 命中结果（沙箱返回的简单结构）。"""

    path: str
    line_number: int
    line: str


@dataclass
class AioSandbox:
    """all-in-one-sandbox 容器客户端封装。

    属性：
        id:        沙箱实例唯一标识（容器名后缀）
        base_url:  沙箱 HTTP API 地址（如 http://127.0.0.1:49153）
        home_dir:  容器内工作目录（/root 或 /home/user 之类）
        version:   容器内服务版本（用于校验镜像新旧）
    """

    id: str
    base_url: str
    home_dir: str | None = None
    version: str | None = None

    bash_output_max_chars: int = 30000
    read_file_output_max_chars: int = 50000
    ls_output_max_chars: int = 30000

    bash_command_timeout: int = 600

    _client: AioSandboxClient | None = field(default=None, repr=False)
    _closed: bool = field(default=False, repr=False)
    _home_dir_fetched: bool = field(default=False, repr=False)
    _workspace_ensured: bool = field(default=False, repr=False)


    def _get_client(self) -> AioSandboxClient:
        """懒创建 SDK 客户端（首次调用时创建）。"""
        if self._closed:
            raise SandboxConnectionError("沙箱已关闭", base_url=self.base_url, cause=None)
        if self._client is not None:
            return self._client
        client_timeout = self.bash_command_timeout or 600
        try:
            self._client = AioSandboxClient(
                base_url=self.base_url,
                timeout=client_timeout,
                httpx_client=httpx.Client(timeout=client_timeout, follow_redirects=True, trust_env=False),
            )
        except Exception as exc:  # noqa: BLE001 - 构造失败一律按连接错误处理
            raise SandboxConnectionError(
                f"初始化沙箱客户端失败: {exc}", base_url=self.base_url, cause=exc
            ) from exc
        return self._client

    def close(self) -> None:
        """关闭底层 HTTP 连接（幂等）。

        Fern SDK 没有暴露 close()，这里拆开内部属性链取出真正的
        httpx.Client 关闭，释放连接池与套接字。
        """
        if self._closed:
            return
        self._closed = True
        client, self._client = self._client, None

        if client is not None:
            wrapper = getattr(client, "_client_wrapper", None)
            fern_http = getattr(wrapper, "httpx_client", None)
            real_httpx = getattr(fern_http, "httpx_client", None)
            target = next(
                (c for c in (real_httpx, fern_http, client) if c is not None and hasattr(c, "close")),
                None,
            )
            if target is not None:
                try:
                    target.close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响调用方
                    logger.debug("关闭沙箱 %s 客户端连接失败", self.id)


    def fetch_home_dir(self) -> str:
        """向沙箱查询 home_dir（首次查询后缓存）。"""
        if self.home_dir is not None:
            return self.home_dir
        if self._home_dir_fetched:
            return self.home_dir or "/"
        self._home_dir_fetched = True
        try:
            ctx = self._get_client().sandbox.get_context()
            self.home_dir = ctx.home_dir
            self.version = ctx.version
            return self.home_dir
        except Exception as exc:  # noqa: BLE001
            logger.warning("获取沙箱 home_dir 失败: %s", exc)
            return self.home_dir or "/"


    def _ensure_workspace(self) -> None:
        """确保容器内工作区根目录 /mnt/user-data 存在且当前用户可写。

        容器未挂载宿主工作区（lifecycle 未传 host_workspace_dir）时，
        /mnt/user-data 不存在；镜像默认用户 gem 无权限在 root 属主的
        /mnt 下创建它（mkdir -p 直接 Permission denied）。这里用
        沙箱内 sudo 一次性建好并 chown 给当前用户，之后所有文件工具
        才能正常读写。幂等：已存在则跳过。
        """
        if self._workspace_ensured:
            return
        guard = self.exec_command(
            f"test -d {VIRTUAL_PATH_PREFIX} && echo EXISTS || echo MISSING"
        )
        if "EXISTS" not in guard:
            self.exec_command(
                f"sudo mkdir -p {VIRTUAL_PATH_PREFIX} && "
                f"sudo chown $(id -u):$(id -g) {VIRTUAL_PATH_PREFIX} && "
                "echo WORKSPACE_READY"
            )
            after = self.exec_command(f"test -d {VIRTUAL_PATH_PREFIX} && echo EXISTS")
            if "EXISTS" not in after:
                logger.warning("沙箱工作区创建失败，后续路径操作可能报错: %s", after)
        self._workspace_ensured = True

    @property
    def workspace_dir(self) -> str:
        """容器内工作区路径（所有沙箱工具默认操作的工作目录）。"""
        return VIRTUAL_PATH_PREFIX


    def _validate_path(self, path: str, *, for_write: bool = False) -> str:
        """校验沙箱内路径：必须在允许的挂载区内且无 .. 穿越。

        允许的区域：
            1. `/mnt/user-data`  —— 可写工作区（per-thread 隔离）
            2. `/mnt/skills`     —— 只读技能目录（全线程共享）
                                   仅在 for_write=False 时放行

        规则：
            1. 必须是以允许前缀开头的绝对路径
            2. 路径中各段不允许出现 ..（防止穿越到挂载区之外）
            3. 返回 POSIX 风格路径，满足沙箱内文件系统要求

        为什么技能目录要单独放行（2026-09-24）：
            技能正文与脚本里引用 `/mnt/skills/public/<name>/references/*.md`、
            `scripts/*.py`。此前校验只认 `/mnt/user-data`，模型一读技能自带资源
            就抛 SandboxPathError；而 `exec_command` 只校验 exec_dir、不校验命令串，
            于是出现「脚本能跑、但读不到脚本说明」的割裂状态。

        为什么写入必须拒绝：
            该挂载在 lifecycle 里是 `readonly` 的，写操作到容器层也会失败；
            在应用层提前拦下能给出更清晰的错误，并避免模型反复重试。

        Raises:
            SandboxPathError: 路径越界或格式非法
        """
        if not isinstance(path, str) or not path.strip():
            raise SandboxPathError("路径不能为空", path=path)
        normalized = path.replace("\\", "/").strip().rstrip("/") or "/"

        allowed_prefixes = [VIRTUAL_PATH_PREFIX.rstrip("/")]
        if not for_write:
            allowed_prefixes.append(SKILLS_CONTAINER_PREFIX.rstrip("/"))

        matched = next(
            (
                prefix
                for prefix in allowed_prefixes
                if normalized == prefix or normalized.startswith(prefix + "/")
            ),
            None,
        )
        if matched is None:
            if for_write:
                raise SandboxPathError(
                    f"不可写入 {path!r}：只有 {VIRTUAL_PATH_PREFIX} 可写"
                    f"（{SKILLS_CONTAINER_PREFIX} 是只读技能目录）",
                    path=path,
                )
            raise SandboxPathError(
                f"路径必须在 {' 或 '.join(allowed_prefixes)} 之下: {path!r}",
                path=path,
            )

        for segment in normalized[len(matched) + 1 :].split("/"):
            if segment == "..":
                raise SandboxPathError(f"路径不允许 .. 穿越: {path!r}", path=path)
            if segment in (".", ""):
                continue
        return normalized

    def _format_error(self, exc: Exception) -> str:
        """把 SDK 异常转成对模型友好的错误消息。"""
        if isinstance(exc, ApiError):
            status = exc.status_code
            body = exc.body
            return f"沙箱 API 错误 (HTTP {status}): {body}"
        return f"沙箱错误: {exc}"


    def exec_command(self, command: str, *, exec_dir: str | None = None) -> str:
        """在容器内执行 bash 命令。

        行为：
            - 命令非零退出 → 返回包含退出码与输出的文本（不抛异常，
              让模型自行修正）
            - 网络/连接错误 → 抛 SandboxConnectionError
            输出按 bash_output_max_chars 截断。

        Args:
            command:  要执行的 bash 命令
            exec_dir: 执行目录（容器内绝对路径，必须在 /mnt/user-data 下）

        Returns:
            命令输出文本（含退出码信息）
        """
        cwd = None
        if exec_dir:
            cwd = self._validate_path(exec_dir)

        with _shell_lock:
            try:
                client = self._get_client()
                result = client.shell.exec_command(
                    command=command,
                    exec_dir=cwd,
                    no_change_timeout=self.bash_command_timeout,
                    truncate=True,
                )
                data = result.data if result else None
            except ApiError as exc:
                if exc.status_code and 400 <= exc.status_code < 500:
                    return self._format_error(exc)
                raise SandboxConnectionError(
                    f"沙箱命令执行失败 (HTTP {exc.status_code})",
                    base_url=self.base_url,
                    cause=exc,
                ) from exc
            except Exception as exc:  # noqa: BLE001 - SDK 抛出的各类连接错误
                raise SandboxConnectionError(
                    f"沙箱命令执行异常: {exc}", base_url=self.base_url, cause=exc
                ) from exc

        if data is None:
            return "(no output)"
        output = data.output if data.output else ""
        exit_code = data.exit_code
        if len(output) > self.bash_output_max_chars:
            output = (
                output[: self.bash_output_max_chars]
                + f"\n...[输出已截断，共 {len(output)} 字符]"
            )
        if exit_code is not None and exit_code != 0:
            return f"[退出码 {exit_code}]\n{output}" if output else f"[退出码 {exit_code}] (无输出)"
        return output if output else "(no output)"


    def read_file(
        self, path: str, *, start_line: int | None = None, end_line: int | None = None
    ) -> str:
        """读取容器内文件内容。

        Args:
            path:       文件路径（/mnt/user-data 下）
            start_line: 起始行（1 起，含）
            end_line:   结束行（1 起，含）

        Returns:
            文件内容文本（可能按 read_file_output_max_chars 截断）
        """
        safe_path = self._validate_path(path)
        try:
            client = self._get_client()
            kwargs = {}
            if start_line is not None:
                kwargs["start_line"] = max(start_line - 1, 0)
            if end_line is not None:
                kwargs["end_line"] = max(end_line, 0)
            result = client.file.read_file(file=safe_path, **kwargs)
            content = result.data.content if result.data else ""
        except ApiError as exc:
            raise SandboxFileError(
                f"读取文件失败 (HTTP {exc.status_code}): {exc.body}",
                path=safe_path,
                operation="read",
            ) from exc
        except SandboxPathError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SandboxFileError(
                f"读取文件异常: {exc}", path=safe_path, operation="read"
            ) from exc
        if len(content) > self.read_file_output_max_chars:
            content = (
                content[: self.read_file_output_max_chars]
                + f"\n...[输出已截断，共 {len(content)} 字符]"
            )
        return content

    def write_file(self, path: str, content: str, *, append: bool = False) -> str:
        """向容器内写入文件（自动创建父目录）。

        Args:
            path:    目标文件路径（/mnt/user-data 下）
            content: 文件内容
            append:  True 追加，False 覆盖

        Returns:
            成功消息文本
        """
        # for_write=True：技能目录是只读挂载，写入必须被拒绝
        safe_path = self._validate_path(path, for_write=True)
        try:
            client = self._get_client()
            self._ensure_workspace()
            parent = safe_path.rsplit("/", 1)[0]
            with _shell_lock:
                client.shell.exec_command(command=f"mkdir -p {parent}", truncate=True)
            result = client.file.write_file(file=safe_path, content=content, append=append)
            return f"已{'追加' if append else '写入'}到 {safe_path}"
        except ApiError as exc:
            raise SandboxFileError(
                f"写入文件失败 (HTTP {exc.status_code}): {exc.body}",
                path=safe_path,
                operation="write",
            ) from exc
        except SandboxPathError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SandboxFileError(
                f"写入文件异常: {exc}", path=safe_path, operation="write"
            ) from exc

    def glob(self, path: str, pattern: str, *, max_results: int = 200) -> list[str]:
        """在容器内按 glob 模式查找文件。

        Args:
            path:    搜索根目录（/mnt/user-data 下）
            pattern: glob 模式（如 "**/*.py"）
            max_results: 结果上限

        Returns:
            命中的文件绝对路径列表（容器内）
        """
        safe_path = self._validate_path(path)
        try:
            client = self._get_client()
            result = client.file.glob_files(
                path=safe_path,
                pattern=pattern,
                files_only=True,
                max_results=max_results,
            )
            data = result.data if result else None
            files = data.files if data and data.files else []
            out = [f.path for f in files if f.path]
            return out[:max_results]
        except ApiError as exc:
            raise SandboxFileError(
                f"查找文件失败 (HTTP {exc.status_code}): {exc.body}",
                path=safe_path,
                operation="glob",
            ) from exc
        except SandboxPathError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SandboxFileError(
                f"查找文件异常: {exc}", path=safe_path, operation="glob"
            ) from exc

    def file_exists(self, path: str) -> bool | None:
        """判断容器内目标文件是否已存在。

        用途：**「写前读」护栏判断这次写是「新建」还是「覆盖」**——
        目标本来就不存在时没有可覆盖的内容，应当直接放行，不该拦着让模型
        先 read（实测 2026-09-25：拦下来会让模型陷入重试死循环）。

        实现：对父目录做一次精确文件名的 glob（比 read_file 便宜，且能区分
        「父目录不存在」与「目录在但文件不在」）。

        参数：
            path: 目标文件路径（/mnt/user-data 下）

        返回：
            True  确认已存在
            False 未确认到存在（文件不存在、父目录缺失、或无权限读到该目录）
            None  连接类失败（沙箱已关闭 / 超时 / 网络）→ 调用方应保守按「已存在」处理

        为什么把「父目录缺失 / 无权限」也算 False 而不算「未知」：
            这两种情况下这次写入要么是在建新文件、要么写入本身也会失败，
            不存在「把已有内容覆盖掉」的风险；而连接类失败必须返回 None，
            否则一次网络抖动就会让护栏失效、放行真正的覆盖写。
            判据来自实测（2026-09-25，镜像 1.11.0）：
              · 目录在、文件在   → glob 成功、1 条匹配
              · 目录在、文件不在 → glob 成功、0 条匹配
              · 父目录不存在     → 服务端 success=false，响应缺字段，
                                   SDK 抛 "validation error for ResponseFileGlobResult"
                                   （文本里带 FileNotFoundError）
        """
        parent, _, name = path.rstrip("/").rpartition("/")
        if not parent or not name:
            return None
        try:
            found = self.glob(parent, name, max_results=1)
        except SandboxFileError as exc:
            text = str(exc)
            # ① 连接类失败：判不出、也不能猜 → 未知
            if any(k in text for k in ("沙箱已关闭", "连接", "超时", "timeout", "Connection")):
                return None
            # ② 目录不存在 / 读不到目录：按「未确认存在」处理（见上文理由）
            return False
        except SandboxError:
            return None
        return bool(found)

    def grep(
        self,
        path: str,
        pattern: str,
        *,
        case_sensitive: bool = False,
        literal: bool = False,
        max_results: int = 100,
    ) -> tuple[list[GrepMatch], bool]:
        """在容器内按正则搜索文件内容。

        Args:
            path:            搜索根目录或单文件（/mnt/user-data 下）
            pattern:         正则或字面量模式
            case_sensitive:  True 区分大小写
            literal:         True 把 pattern 当普通字符串
            max_results:     结果上限

        Returns:
            (命中列表, 是否被截断)
        """
        safe_path = self._validate_path(path)
        try:
            client = self._get_client()
            result = client.file.grep_files(
                path=safe_path,
                pattern=pattern,
                case_insensitive=not case_sensitive,
                fixed_strings=literal,
                max_results=max_results * 2,
                recursive=True,
            )
            data = result.data if result else None
            raw_matches = data.matches if data and data.matches else []
            truncated = bool(data and data.truncated)
        except ApiError as exc:
            raise SandboxFileError(
                f"搜索内容失败 (HTTP {exc.status_code}): {exc.body}",
                path=safe_path,
                operation="grep",
            ) from exc
        except SandboxPathError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SandboxFileError(
                f"搜索内容异常: {exc}", path=safe_path, operation="grep"
            ) from exc

        matches: list[GrepMatch] = []
        for m in raw_matches:
            matches.append(
                GrepMatch(
                    path=getattr(m, "file", ""),
                    line_number=getattr(m, "line_number", 0),
                    line=getattr(m, "line_content", ""),
                )
            )
            if len(matches) >= max_results:
                truncated = True
                break
        return matches, truncated

    def list_dir(self, path: str, *, max_depth: int = 2) -> list[str]:
        """列出容器内目录内容（浅层）。

        Args:
            path:      目录路径（/mnt/user-data 下）
            max_depth: 递归深度（默认 2）

        Returns:
            子项路径列表（容器内绝对路径）
        """
        safe_path = self._validate_path(path)
        try:
            client = self._get_client()
            result = client.file.list_path(path=safe_path, max_depth=max_depth)
            data = result.data if result else None
            files = data.files if data and data.files else []
            return [f.path for f in files if f.path]
        except ApiError as exc:
            raise SandboxFileError(
                f"列目录失败 (HTTP {exc.status_code}): {exc.body}",
                path=safe_path,
                operation="list",
            ) from exc
        except SandboxPathError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SandboxFileError(
                f"列目录异常: {exc}", path=safe_path, operation="list"
            ) from exc


def generate_sandbox_id(prefix: str = "prism-sbx") -> str:
    """生成沙箱实例唯一 id（容器名后缀用）。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def wait_for_sandbox_ready(
    base_url: str, *, timeout: float = 60.0, interval: float = 0.5
) -> bool:
    """轮询沙箱 HTTP 服务直到就绪（容器端口映射生效）。

    Args:
        base_url: 沙箱 API 地址
        timeout:  最长等待秒数
        interval: 探测间隔秒数

    Returns:
        True 就绪 / False 超时
    """
    deadline = time.monotonic() + timeout
    probe_client: AioSandboxClient | None = None
    while time.monotonic() < deadline:
        try:
            if probe_client is None:
                probe_client = AioSandboxClient(
                    base_url=base_url,
                    timeout=5,
                    httpx_client=httpx.Client(timeout=5, follow_redirects=True, trust_env=False),
                )
            ctx = probe_client.sandbox.get_context()
            if ctx and ctx.home_dir:
                return True
        except Exception:  # noqa: BLE001 - 未就绪时忽略错误继续等待
            pass
        time.sleep(interval)
    return False