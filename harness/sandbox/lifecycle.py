"""沙箱容器生命周期管理（docker CLI）。

通过宿主机 docker CLI 管理 all-in-one-sandbox 容器：
    - start()        —— 拉镜像 → 起容器 → 等就绪 → 返回 AioSandbox
    - stop()         —— 停止并删除容器（auto 模式）
    - ensure_stopped —— 幂等地停止所有本项目容器

关键点：
    1. docker 命令前缀可配置（docker_command），Windows 上 docker 在
       WSL 内，通常需要 ["wsl", "-e", "docker"]。
    2. 工作区目录通过 -v 挂载进容器，容器内路径固定为 /mnt/user-data。
    3. replicas>1 时维护一个小型「沙箱池」，每个线程固定分到一台。
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field

from harness.config.paths import VIRTUAL_PATH_PREFIX
from harness.config.sandbox_config import SandboxConfig
from harness.sandbox.aio_sandbox import AioSandbox, wait_for_sandbox_ready

logger = logging.getLogger(__name__)

_CONTAINER_API_PORT = 8080

_CONTAINER_MOUNT_POINT = VIRTUAL_PATH_PREFIX


@dataclass
class SandboxManager:
    """容器生命周期管理器。

    通过 docker CLI 管理沙箱容器。auto 模式下：
        - 首次 start() 时拉取镜像并启动容器
        - 空闲超过 idle_timeout 的容器会被自动回收（回收由 stop() 触发）
    manual 模式下：用户已自行启动容器，只用 base_url 直接连接。

    属性：
        config:      沙箱配置
        docker_cmd:  完整的 docker CLI 前缀（默认探测 wsl）
    """

    config: SandboxConfig = field(default_factory=SandboxConfig)

    _sandboxes: dict[str, AioSandbox] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _docker_cmd: list[str] | None = field(default=None, repr=False)


    def _resolve_docker_cmd(self) -> list[str]:
        """返回 docker 命令前缀（含自动探测）。

        优先级：
            1. config.docker_command（显式配置）
            2. 探测 which docker / wsl -e docker
        """
        if self._docker_cmd is not None:
            return self._docker_cmd
        if self.config.docker_command:
            self._docker_cmd = list(self.config.docker_command)
            return self._docker_cmd
        import shutil

        for candidate in (["docker"], ["wsl", "-e", "docker"]):
            try:
                probe = subprocess.run(
                    candidate + ["--version"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if probe.returncode == 0:
                    self._docker_cmd = candidate
                    logger.info("使用 docker CLI 前缀: %s", candidate)
                    return candidate
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        raise RuntimeError(
            "未找到可用的 docker CLI。请在 config.yaml 中配置 sandbox.docker_command，"
            "例如 Windows 上 ['wsl', '-e', 'docker']。"
        )

    def _run_docker(self, *args: str, check: bool = True, text_output: bool = True) -> subprocess.CompletedProcess:
        """执行 docker CLI 命令。

        Args:
            *args: docker 子命令参数
            check: True 时非零退出码抛 CalledProcessError

        Returns:
            子进程结果
        """
        cmd = self._resolve_docker_cmd() + list(args)
        proc = subprocess.run(cmd, capture_output=True, text=text_output, timeout=300)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr
            )
        return proc

    def _container_name(self, instance_id: str) -> str:
        """生成容器名：前缀-实例id。"""
        return f"{self.config.container_prefix}-{instance_id}"


    def start(self, *, host_workspace_dir: str | None = None) -> AioSandbox:
        """启动（或复用）一个沙箱容器，返回 AioSandbox 实例。

        - lifecycle=manual：直接连 config.base_url，不管理容器
        - lifecycle=auto：  拉镜像 → docker run → 等就绪 → 返回

        Args:
            host_workspace_dir: 宿主机工作区目录（挂载进容器）。

        Returns:
            AioSandbox 实例
        """
        if self.config.lifecycle == "manual":
            return self._connect_manual()

        instance_id = uuid.uuid4().hex[:12]
        name = self._container_name(instance_id)

        self._ensure_image()

        run_args = [
            "run", "-d", "--name", name,
            "-p", f"{self.config.bind_host}::{_CONTAINER_API_PORT}",
        ]
        if host_workspace_dir:
            run_args += ["-v", f"{host_workspace_dir}:{_CONTAINER_MOUNT_POINT}"]
        for m in self.config.mounts:
            src = m.get("source") or m.get("src")
            dst = m.get("target") or m.get("dest")
            if src and dst:
                run_args += ["-v", f"{src}:{dst}"]
        for k, v in self.config.environment.items():
            run_args += ["-e", f"{k}={v}"]
        run_args += [self.config.image]

        try:
            self._run_docker(*run_args)
        except subprocess.CalledProcessError as exc:
            if "already in use" in (exc.stderr or "").lower():
                logger.warning("容器名冲突，清理后重建: %s", name)
                self._run_docker("rm", "-f", name, check=False)
                self._run_docker(*run_args)
            else:
                raise RuntimeError(f"docker run 失败: {exc.stderr}") from exc

        base_url = self._query_port(name)

        if not wait_for_sandbox_ready(base_url, timeout=120):
            self.stop(sandbox_id=instance_id)
            raise RuntimeError(f"沙箱 {name} 启动超时未就绪")

        sbx = AioSandbox(
                id=instance_id,
                base_url=base_url,
                bash_output_max_chars=self.config.bash_output_max_chars,
                read_file_output_max_chars=self.config.read_file_output_max_chars,
                ls_output_max_chars=self.config.ls_output_max_chars,
                bash_command_timeout=self.config.bash_command_timeout,
            )
        with self._lock:
            self._sandboxes[instance_id] = sbx
        logger.info("沙箱 %s 就绪: %s", name, base_url)
        return sbx

    def _connect_manual(self) -> AioSandbox:
        """manual 模式：直接连接已有容器。"""
        base_url = self.config.base_url
        if not base_url:
            raise RuntimeError(
                "sandbox.lifecycle=manual 时必须配置 sandbox.base_url "
                "（指向已启动容器的 HTTP 端口）"
            )
        return AioSandbox(
            id="manual",
            base_url=base_url,
            bash_output_max_chars=self.config.bash_output_max_chars,
            read_file_output_max_chars=self.config.read_file_output_max_chars,
            ls_output_max_chars=self.config.ls_output_max_chars,
            bash_command_timeout=self.config.bash_command_timeout,
        )

    def _ensure_image(self) -> None:
        """确保沙箱镜像存在；不存在则 docker pull。"""
        probe = self._run_docker("image", "inspect", self.config.image, check=False)
        if probe.returncode == 0:
            return
        logger.info("拉取沙箱镜像 %s ...", self.config.image)
        self._run_docker("pull", self.config.image)

    def _query_port(self, name: str) -> str:
        """通过 docker port 查询容器映射的宿主机端口。"""
        proc = self._run_docker("port", name)
        out = (proc.stdout or "").strip()
        for line in out.splitlines():
            if str(_CONTAINER_API_PORT) in line:
                addr = line.split("->", 1)[-1].strip()
                host, port = addr.rsplit(":", 1)
                host = host.strip() or "127.0.0.1"
                if host in ("0.0.0.0", "[::]", "::"):
                    host = "127.0.0.1"
                return f"http://{host}:{port.strip()}"
        raise RuntimeError(f"无法从 docker port 查询端口: {out}")

    def stop(self, sandbox_id: str | AioSandbox | None = None) -> None:
        """停止并删除沙箱容器（auto 模式）。

        Args:
            sandbox_id: 实例 id 或 AioSandbox；None 时停止全部
        """
        targets: list[str] = []
        if isinstance(sandbox_id, AioSandbox):
            targets = [sandbox_id.id]
        elif sandbox_id is not None:
            targets = [sandbox_id]
        else:
            targets = list(self._sandboxes.keys())

        for sid in targets:
            sbx = self._sandboxes.pop(sid, None)
            if sbx is not None:
                try:
                    sbx.close()
                except Exception:  # noqa: BLE001
                    pass
            name = self._container_name(sid)
            self._run_docker("rm", "-f", name, check=False)
            logger.info("沙箱容器 %s 已回收", name)

    def cleanup_all(self) -> None:
        """回收本管理器创建的所有沙箱（程序退出时调用）。"""
        self.stop()


    def get(self, sandbox_id: str) -> AioSandbox | None:
        """按实例 id 取回沙箱实例。"""
        return self._sandboxes.get(sandbox_id)

    def available(self) -> list[AioSandbox]:
        """返回当前存活的沙箱列表。"""
        return list(self._sandboxes.values())



_default_manager: SandboxManager | None = None
_manager_lock: threading.Lock = threading.Lock()


def get_sandbox_manager(config: SandboxConfig | None = None) -> SandboxManager:
    """返回进程级沙箱管理器单例。

    主代理工厂与子代理执行器都通过它拿 SandboxManager，才能取到同一个
    容器实例（子代理按 sandbox_id 从管理器缓存里取回 AioSandbox）。

    Args:
        config: 首次创建时使用的沙箱配置；缺省取全局应用配置的 sandbox。
    """
    global _default_manager
    if _default_manager is not None:
        return _default_manager
    with _manager_lock:
        if _default_manager is None:
            if config is None:
                from harness.config.app_config import get_app_config

                config = get_app_config().sandbox
            _default_manager = SandboxManager(config=config)
            logger.info("初始化进程级沙箱管理器（lifecycle=%s）", config.lifecycle)
    return _default_manager