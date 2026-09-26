"""沙箱容器生命周期管理（docker CLI）+ 热池（Warm Pool）。

通过宿主机 docker CLI 管理 all-in-one-sandbox 容器：
    - start()        —— 取用沙箱：活跃缓存命中 → 热池提升 → 新建（带容量闸门）
    - release()      —— run 结束后回源热池（容器保活，零冷启动复用）
    - stop()         —— 停止并删除容器（auto 模式；覆盖活跃与热池）
    - ensure_stopped —— 幂等地停止所有本项目容器

热池机制（对齐参考实现的 warm pool 协议，单进程简化版）：
    1. 确定性 sandbox_id：调用方传 sandbox_id（如按 thread_id 派生）时，
       同 id 下次取用直接复用（活跃缓存 / 热池提升），容器不被重复创建；
    2. 热池回源：release() 把沙箱从活跃摘除放入热池，容器与客户端实例
       均保持运行（实例可原样复用），供同线程下轮快速取回；
    3. idle 回收：idle checker 守护线程每 60s 清理闲置超过
       idle_timeout 的**热池**条目并销毁容器（配置字段真正生效）；
    4. 容量闸门：活跃 + 热池达到 replicas 上限时新取用先逐出最旧热池
       条目；热池已空则「共享回退」——借用现有活跃容器（引用计数，
       借用者在场时不允许回源销毁），保证 replicas=1 时进程内仍只有
       一台容器（与旧共享语义一致）；
    5. 孤儿收编：定期扫描运行中但未被本进程跟踪的同前缀容器
       （进程崩溃遗留），健康后收编进热池复用。

关键点：
    1. docker 命令前缀可配置（docker_command），Windows 上 docker 在
       WSL 内，通常需要 ["wsl", "-e", "docker"]。
    2. 挂载统一用 `--mount type=bind,src=..,dst=..[,readonly]`（不用 `-v`：
       `-v` 用冒号分隔，与 Windows 盘符路径 `E:\\...` 撞语义）。
       工作区挂到 /mnt/user-data（可写，per-thread），技能根挂到 /mnt/skills
       （只读，全线程共享同一份宿主目录）。
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from harness.config.paths import PROJECT_ROOT, VIRTUAL_PATH_PREFIX
from harness.config.sandbox_config import SandboxConfig
from harness.config.skills import SkillsConfig
from harness.sandbox.aio_sandbox import AioSandbox, wait_for_sandbox_ready
from harness.sandbox.warm_pool import WarmPool

logger = logging.getLogger(__name__)

_CONTAINER_API_PORT = 8080

_CONTAINER_MOUNT_POINT = VIRTUAL_PATH_PREFIX


def _windows_to_wsl_path(host_path: str) -> str:
    """把 Windows 盘符路径手工映射成 WSL 路径：`E:\\a\\b` → `/mnt/e/a/b`。

    这是 `wslpath` 不可用时的兜底（标准 WSL 都把盘符挂在 /mnt/<小写盘符> 下）。
    非盘符路径只做分隔符归一，原样返回，避免把 Linux 路径改坏。
    """
    if len(host_path) >= 2 and host_path[1] == ":" and host_path[0].isalpha():
        drive = host_path[0].lower()
        rest = host_path[2:].lstrip("\\/").replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return host_path.replace("\\", "/")

# idle checker 周期（秒）：清理闲置热池条目 + 周期性孤儿扫描
_IDLE_CHECK_INTERVAL_SECONDS = 60

# 孤儿扫描周期：每 N 轮 idle 检查执行一次
_ORPHAN_SCAN_EVERY_N_ROUNDS = 3


@dataclass
class SandboxManager:
    """容器生命周期管理器（含热池保活复用与闲置治理）。

    auto 模式下：
        - start() 优先复用（活跃缓存 → 热池提升），无可用容器才新建
        - 闲置超过 idle_timeout 的热池容器由守护线程自动销毁
        - 容器总量受 replicas 软上限约束
    manual 模式下：用户已自行启动容器，只用 base_url 直接连接。

    属性：
        config:      沙箱配置
        docker_cmd:  完整的 docker CLI 前缀（默认探测 wsl）
    """

    config: SandboxConfig = field(default_factory=SandboxConfig)

    _sandboxes: dict[str, AioSandbox] = field(default_factory=dict, repr=False)
    _warm_pool: WarmPool = field(default_factory=WarmPool, repr=False)
    # 共享回退借用计数：sandbox_id → 正在借用该容器的调用方数量
    _borrowers: dict[str, int] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _docker_cmd: list[str] | None = field(default=None, repr=False)
    _idle_checker: threading.Thread | None = field(default=None, repr=False)
    _checker_stop: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _checker_started: bool = field(default=False, repr=False)
    _checker_round: int = field(default=0, repr=False)


    def _resolve_docker_cmd(self) -> list[str]:
        """返回 docker 命令前缀（含自动探测）。

        优先级：
            1. config.docker_command（显式配置）
            2. 探测 docker / wsl -e docker

        探测失败要区分三类原因，并全部汇总进最终报错——实测（2026-09-23）
        `wsl.exe` 被安全策略拦截时抛的是 `PermissionError [WinError 5]`，
        它既不是 FileNotFoundError 也不是 TimeoutExpired；旧代码没接住它，
        导致这个原始异常直接穿透到上层，日志只剩一句「[WinError 5] 拒绝访问」，
        完全看不出是「哪个候选命令失败、为什么失败」。这里把三种异常统一
        收进失败清单，最终抛一条能直接照着排查的 RuntimeError。
        """
        if self._docker_cmd is not None:
            return self._docker_cmd
        if self.config.docker_command:
            self._docker_cmd = list(self.config.docker_command)
            return self._docker_cmd

        candidates = (["docker"], ["wsl", "-e", "docker"])
        failures: list[str] = []
        for candidate in candidates:
            label = " ".join(candidate)
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
                failures.append(f"{label} → 退出码 {probe.returncode}")
            except PermissionError:
                # 被安全策略（程序黑名单）拦截：可执行文件在，但禁止启动
                failures.append(f"{label} → 被安全策略拦截（PermissionError，疑似程序黑名单）")
            except FileNotFoundError:
                failures.append(f"{label} → 可执行文件不存在")
            except subprocess.TimeoutExpired:
                failures.append(f"{label} → 探测超时")

        detail = "；".join(failures) if failures else "无可用候选"
        raise RuntimeError(
            f"未找到可用的 docker CLI（{detail}）。"
            "请在 config.yaml 中配置 sandbox.docker_command，"
            "例如 Windows 上 ['wsl', '-e', 'docker']；"
            "若已配置但仍被拦截，请检查安全中心的程序黑名单是否包含 wsl.exe。"
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


    def _make_sandbox(self, instance_id: str, base_url: str) -> AioSandbox:
        """构造 AioSandbox 实例（配置项统一注入点）。"""
        return AioSandbox(
            id=instance_id,
            base_url=base_url,
            bash_output_max_chars=self.config.bash_output_max_chars,
            read_file_output_max_chars=self.config.read_file_output_max_chars,
            ls_output_max_chars=self.config.ls_output_max_chars,
            bash_command_timeout=self.config.bash_command_timeout,
        )

    def _container_alive(self, name: str) -> bool:
        """快速检查容器是否仍在运行（docker inspect）。"""
        probe = self._run_docker(
            "inspect", "-f", "{{.State.Running}}", name, check=False
        )
        return probe.returncode == 0 and (probe.stdout or "").strip() == "true"


    # ── 取用 ────────────────────────────────────────────────────────────
    def start(
        self,
        *,
        host_workspace_dir: str | None = None,
        sandbox_id: str | None = None,
    ) -> AioSandbox:
        """取用一个沙箱容器，返回 AioSandbox 实例。

        取用顺序（auto 模式）：
            1. 活跃缓存命中（含共享回退的借用实例）→ 直接返回；
            2. 热池提升：同 sandbox_id 在热池中 → 健康检查通过则复用
               （零冷启动），容器已消亡则清理后新建；
            3. 新建：容量闸门（replicas 上限，满则先逐出最旧热池条目；
               热池空则共享回退现有活跃容器）→ docker run → 等就绪。

        Args:
            host_workspace_dir: 宿主机工作区目录（挂载进容器）。
            sandbox_id: 确定性沙箱 id（如按 thread_id 派生）；缺省随机生成。

        Returns:
            AioSandbox 实例
        """
        if self.config.lifecycle == "manual":
            return self._connect_manual()

        target_id = sandbox_id or uuid.uuid4().hex[:12]

        # 1. 活跃缓存命中（幂等复用；共享回退借用的实例也在此）
        with self._lock:
            active = self._sandboxes.get(target_id)
        if active is not None:
            logger.debug("沙箱 %s 命中活跃缓存，直接复用", target_id)
            return active

        # 2. 热池提升（健康检查：容器存活 + HTTP 快速探测）
        warm_instance = self._warm_pool.peek(target_id)
        if warm_instance is not None:
            name = self._container_name(target_id)
            if self._container_alive(name):
                try:
                    ready = wait_for_sandbox_ready(warm_instance.base_url, timeout=5)
                except Exception:  # noqa: BLE001 —— 探测失败按失效处理
                    ready = False
                if ready:
                    reclaimed = self._warm_pool.reclaim(target_id)
                    if reclaimed is not None:
                        with self._lock:
                            self._sandboxes[target_id] = reclaimed
                        logger.info(
                            "沙箱 %s 从热池提升复用（零冷启动）: %s",
                            target_id,
                            reclaimed.base_url,
                        )
                        return reclaimed
            logger.warning(
                "热池沙箱 %s 已失效（容器或服务异常），销毁后重建", target_id
            )
            stale = self._warm_pool.remove(target_id)
            if stale is not None:
                self._destroy_container(stale)

        # 3. 容量闸门 + 共享回退（replicas 软上限）
        with self._lock:
            occupied = len(self._sandboxes) + len(self._warm_pool)
            replicas = self.config.replicas
            if replicas and replicas > 0 and occupied >= replicas:
                evicted = self._warm_pool.evict_oldest()
                if evicted is not None:
                    self._destroy_container(evicted)
                    logger.info(
                        "容量闸门触发，逐出最旧热池沙箱 %s（replicas=%d）",
                        evicted.id,
                        replicas,
                    )
                    occupied -= 1
            if replicas and replicas > 0 and occupied >= replicas:
                # 热池已无可逐出 → 共享回退：借用现有活跃容器。
                #
                # ⚠️ 仅在**没有 per-thread 挂载**时才允许借用（2026-09-23 修正）：
                #    容器会挂载「所属线程」的 user-data 目录（见 _spawn_new 的 -v）。
                #    一旦把线程 A 的容器借给线程 B，B 就直接读写 A 的目录 —— 跨线程
                #    数据泄露。所以绑定了线程目录的容器绝不共享，宁可多起一个：
                #    活跃容器按线程数增长（由并发 run 天然约束），
                #    replicas 退化为「热池保活上限」。
                if host_workspace_dir is None:
                    borrowed = next(iter(self._sandboxes.values()), None)
                    if borrowed is not None:
                        self._borrowers[borrowed.id] = self._borrowers.get(borrowed.id, 0) + 1
                        logger.info(
                            "沙箱容量已满（replicas=%d），%s 共享借用容器 %s",
                            replicas,
                            target_id,
                            borrowed.id,
                        )
                        return borrowed
                else:
                    logger.info(
                        "沙箱容量已满（replicas=%d）且容器绑定了线程工作区，%s 不共享、直接新建",
                        replicas,
                        target_id,
                    )

        return self._spawn_new(target_id, host_workspace_dir=host_workspace_dir)

    def _to_docker_host_path(self, host_path: str) -> str:
        """把宿主路径转成 docker CLI 能理解的路径。

        为什么需要转换（Windows + WSL 场景）：
            Windows 上 docker CLI 往往是通过 `wsl -e docker` 调用的，而 docker
            守护进程跑在 WSL 发行版里、看到的是 Linux 命名空间 ——
            `E:\\Project\\x` 这种盘符路径它认不出来，必须变成 `/mnt/e/Project/x`。
            不转换的话 `-v` 会静默挂载失败或挂到错误位置。

        策略：优先 `wslpath -a`（官方转换，能正确处理自定义挂载根）；
              失败则退回手工盘符映射（标准 WSL 都是 /mnt/<盘符>）。
              非 wsl 调用（如 Windows 原生 docker）原样返回。

        参数：
            host_path: 宿主侧路径（可能是 Windows 盘符路径）

        返回：
            docker 可用的路径字符串
        """
        try:
            docker_cmd = self._resolve_docker_cmd()
        except RuntimeError:
            # 探测不到 docker CLI（例如被安全策略拦截）：无从判断是否 wsl 调用
            docker_cmd = None

        # 经 wsl 调用 docker：路径必须转成 WSL 命名空间的写法
        if docker_cmd and docker_cmd[0] == "wsl":
            try:
                # cmd_prefix 形如 ["wsl","-e","docker"] → ["wsl","-e","wslpath","-a",path]
                probe = subprocess.run(
                    list(docker_cmd[:-1]) + ["wslpath", "-a", host_path],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                converted = (probe.stdout or "").strip()
                if probe.returncode == 0 and converted.startswith("/"):
                    return converted
            except (OSError, subprocess.TimeoutExpired):
                pass
            return _windows_to_wsl_path(host_path)

        # 非 wsl：Windows 原生 docker 认得盘符路径，原样返回；
        # 探测不到 docker CLI 时也兜底转一次，免得把盘符路径塞给容器。
        if docker_cmd:
            return host_path
        return _windows_to_wsl_path(host_path)

    def _resolve_mount_source(self, source: str) -> str:
        """把挂载源解析成宿主绝对路径。

        相对路径以**项目根**为基准（不是 CWD）—— 服务可能从任意目录启动，
        用 CWD 会让同一份配置在不同启动方式下挂到不同地方。
        """
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())

    def _mount_args(
        self,
        source: str,
        target: str,
        *,
        read_only: bool,
        ensure_exists: bool = True,
    ) -> list[str]:
        """生成一条 bind mount 的 docker 参数。

        为什么用 `--mount type=bind,src=...,dst=...[,readonly]` 而不是 `-v src:dst[:ro]`：
            `-v` 用冒号做分隔符，而 Windows 盘符路径（`E:\\...`）本身就含冒号，
            经 WSL 转换后虽然变成 `/mnt/e/...` 没有冒号，但只要有一环没转成功，
            整条挂载就会静默挂错位置。`--mount` 是键值对形式，没有这个歧义。

        代价（必须记住）：`--mount` **不会**像 `-v` 那样自动创建缺失的宿主目录，
        源路径不存在会直接 `docker run` 失败。所以这里默认先 `mkdir -p`。

        Args:
            source:        宿主侧源目录（可为相对路径，以项目根为基准）
            target:        容器内目标路径
            read_only:     True → 追加 `readonly`（技能目录必须为 True）
            ensure_exists: 是否先创建宿主源目录；测试纯格式时可传 False

        Returns:
            docker 参数片段，形如 ["--mount", "type=bind,src=/mnt/e/x,dst=/mnt/skills,readonly"]
        """
        host = self._resolve_mount_source(source)
        if ensure_exists:
            Path(host).mkdir(parents=True, exist_ok=True)
        spec = f"type=bind,src={self._to_docker_host_path(host)},dst={target}"
        if read_only:
            spec += ",readonly"
        return ["--mount", spec]

    def _skills_mount_args(self) -> list[str]:
        """生成技能目录的挂载参数（只读，全线程共享同一份宿主目录）。

        挂载源是整个 skills 根（不是 skills/public），容器内因此得到
        `/mnt/skills/public/<name>/...` —— 与参考实现 DeerFlow 的路径约定完全一致，
        于是 DeerFlow 技能正文里硬编码的
        `/mnt/skills/public/<name>/scripts/xxx.py` **无需改写**即可直接工作。

        开销说明：bind mount 只是让容器看到宿主同一份目录的引用，**不复制数据**，
        所以「每个容器都挂」与「所有线程共享」在磁盘/内存开销上是同一件事；
        真正贵的是 per-thread 容器本身（由 user-data 隔离需求决定，与技能无关）。
        """
        if not self.config.mount_skills:
            return []
        cfg = SkillsConfig()
        root = cfg.resolve_skills_root()
        if not root.is_dir():
            logger.warning("技能目录不存在，本次不挂载技能: %s", root)
            return []
        return self._mount_args(
            str(root), cfg.container_skills_dir(), read_only=True
        )

    def _spawn_new(self, instance_id: str, *, host_workspace_dir: str | None) -> AioSandbox:
        """新建容器（容量闸门已放行）并注册到活跃。"""
        name = self._container_name(instance_id)
        self._ensure_image()

        run_args = [
            "run", "-d", "--name", name,
            "-p", f"{self.config.bind_host}::{_CONTAINER_API_PORT}",
        ]
        # 1. 线程工作区：可写，per-thread 隔离（容器与宿主是同一份文件）
        if host_workspace_dir:
            run_args += self._mount_args(
                host_workspace_dir, _CONTAINER_MOUNT_POINT, read_only=False
            )
        # 2. 技能目录：只读，全线程共享同一份宿主目录
        run_args += self._skills_mount_args()
        # 3. 配置里的额外挂载
        for m in self.config.mounts:
            if m.source and m.target:
                run_args += self._mount_args(
                    m.source, m.target, read_only=m.read_only
                )
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
            self._destroy_container(self._make_sandbox(instance_id, base_url))
            raise RuntimeError(f"沙箱 {name} 启动超时未就绪")

        sbx = self._make_sandbox(instance_id, base_url)
        with self._lock:
            self._sandboxes[instance_id] = sbx
        self._ensure_idle_checker()
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
        return self._make_sandbox("manual", base_url)

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


    # ── 回源 ────────────────────────────────────────────────────────────
    def release(self, sandbox_id: str | AioSandbox) -> None:
        """把沙箱回源热池（容器保活，供同 id 下次零冷启动复用）。

        借用中的容器（共享回退）不回源：借用计数 > 0 时只减计数，
        由最后一个 release 的调用方完成回源，避免销毁仍在使用的容器。

        幂等：目标不在活跃也不在热池时静默跳过。
        """
        if isinstance(sandbox_id, AioSandbox):
            sandbox_id = sandbox_id.id
        with self._lock:
            borrows = self._borrowers.get(sandbox_id, 0)
            if borrows > 0:
                self._borrowers[sandbox_id] = borrows - 1
                logger.info(
                    "沙箱 %s 仍被 %d 个调用方借用，回源推迟",
                    sandbox_id,
                    borrows - 1,
                )
                return
            self._borrowers.pop(sandbox_id, None)
            instance = self._sandboxes.pop(sandbox_id, None)
        if instance is None:
            return
        # 实例不 close：容器与 HTTP 客户端均保活，reclaim 时原样复用
        self._warm_pool.park(instance)
        logger.info("沙箱 %s 已回源热池（容器保活待复用）", sandbox_id)

    def stop(self, sandbox_id: str | AioSandbox | None = None) -> None:
        """停止并删除沙箱容器（auto 模式；活跃与热池一并处理）。

        Args:
            sandbox_id: 实例 id 或 AioSandbox；None 时销毁全部
        """
        if isinstance(sandbox_id, AioSandbox):
            sandbox_id = sandbox_id.id

        with self._lock:
            if sandbox_id is not None:
                active_targets = (
                    [sandbox_id] if sandbox_id in self._sandboxes else []
                )
                warm_targets = (
                    [self._warm_pool.remove(sandbox_id)]
                    if self._warm_pool.contains(sandbox_id)
                    else []
                )
                self._borrowers.pop(sandbox_id, None)
            else:
                active_targets = list(self._sandboxes.keys())
                warm_targets = self._warm_pool.clear()
                self._borrowers.clear()

        for sid in active_targets:
            sbx = self._sandboxes.pop(sid, None)
            self._destroy_container(sbx)
        for instance in warm_targets:
            if instance is not None:
                self._destroy_container(instance)

    def cleanup_all(self) -> None:
        """回收本管理器创建的所有沙箱并停止闲置守护（退出时调用）。"""
        self._stop_idle_checker()
        self.stop()
        self._warm_pool.clear()

    def _destroy_container(self, sbx: AioSandbox | None) -> None:
        """销毁一个沙箱容器（docker rm -f + 实例 close，容错）。"""
        if sbx is None:
            return
        try:
            sbx.close()
        except Exception:  # noqa: BLE001 —— 客户端关闭失败不阻断销毁
            pass
        name = self._container_name(sbx.id)
        self._run_docker("rm", "-f", name, check=False)
        logger.info("沙箱容器 %s 已回收", name)


    # ── 闲置治理与孤儿收编（守护线程） ─────────────────────────────────
    def _ensure_idle_checker(self) -> None:
        """懒启动闲置回收守护线程（幂等）。"""
        if self._checker_started:
            return
        with self._lock:
            if self._checker_started:
                return
            self._checker_started = True
        self._checker_stop.clear()
        self._idle_checker = threading.Thread(
            target=self._idle_checker_loop,
            name="sandbox-idle-checker",
            daemon=True,
        )
        self._idle_checker.start()
        logger.debug("沙箱闲置回收守护线程已启动（周期 %ds）", _IDLE_CHECK_INTERVAL_SECONDS)

    def _stop_idle_checker(self) -> None:
        """停止守护线程（幂等，不 join——daemon 线程无需等待）。"""
        self._checker_stop.set()

    def _idle_checker_loop(self) -> None:
        """守护循环：清理闲置热池条目 + 周期孤儿收编。"""
        while not self._checker_stop.is_set():
            try:
                idle_timeout = float(self.config.idle_timeout or 0)
                expired = self._warm_pool.idle_expired(idle_timeout)
                for instance in expired:
                    logger.info(
                        "热池沙箱 %s 闲置超过 %ds，销毁回收",
                        instance.id,
                        idle_timeout,
                    )
                    self._warm_pool.remove(instance.id)
                    self._destroy_container(instance)

                self._checker_round += 1
                if self._checker_round % _ORPHAN_SCAN_EVERY_N_ROUNDS == 0:
                    try:
                        self._reconcile_orphans()
                    except Exception:  # noqa: BLE001 —— 孤儿扫描失败不阻断周期
                        logger.warning("孤儿沙箱扫描失败（忽略）", exc_info=True)
            except Exception:  # noqa: BLE001 —— 单轮失败不终止守护
                logger.warning("闲置回收轮次异常（忽略）", exc_info=True)
            finally:
                self._checker_stop.wait(_IDLE_CHECK_INTERVAL_SECONDS)

    def _reconcile_orphans(self) -> None:
        """收编运行中但未被本进程跟踪的同前缀容器（崩溃遗留）进热池。

        扫描 docker ps 的同前缀容器；不在活跃也不在热池的，
        端口复用后实例化并入池（健康检查由后续取用兜底）。
        """
        prefix = self.config.container_prefix
        if not prefix:
            return
        proc = self._run_docker(
            "ps", "--filter", f"name={prefix}", "--format", "{{.Names}}",
            check=False,
        )
        if proc.returncode != 0:
            return
        with self._lock:
            tracked_ids = set(self._sandboxes)
            tracked_ids.update(self._warm_pool.snapshot_sandbox_ids())
        adopted = 0
        for line in (proc.stdout or "").splitlines():
            name = line.strip()
            if not name:
                continue
            instance_id = self._extract_instance_id(name, prefix)
            if instance_id is None or instance_id in tracked_ids:
                continue
            try:
                base_url = self._query_port(name)
            except Exception:  # noqa: BLE001 —— 端口查询失败跳过孤儿
                logger.debug("孤儿容器 %s 端口查询失败，跳过", name)
                continue
            orphan = self._make_sandbox(instance_id, base_url)
            self._warm_pool.park(orphan)
            tracked_ids.add(instance_id)
            adopted += 1
        if adopted:
            logger.info("孤儿沙箱收编 %d 台进热池（复用而非重建）", adopted)

    @staticmethod
    def _extract_instance_id(container_name: str, prefix: str) -> str | None:
        """从容器名剥离前缀得到实例 id；非本项目容器返回 None。"""
        if not container_name.startswith(prefix + "-"):
            return None
        instance_id = container_name[len(prefix) + 1 :]
        return instance_id or None


    # ── 查询 ────────────────────────────────────────────────────────────
    def get(self, sandbox_id: str) -> AioSandbox | None:
        """按实例 id 取回沙箱（活跃缓存；热池内不视为可用）。"""
        return self._sandboxes.get(sandbox_id)

    def available(self) -> list[AioSandbox]:
        """返回当前活跃的沙箱列表。"""
        return list(self._sandboxes.values())

    @property
    def warm_count(self) -> int:
        """热池中保活的沙箱数量。"""
        return len(self._warm_pool)

    def warm_snapshot(self) -> list[dict]:
        """热池快照（管理与日志）。"""
        return self._warm_pool.snapshot()

    def borrows(self, sandbox_id: str) -> int:
        """某容器的当前借用计数。"""
        with self._lock:
            return self._borrowers.get(sandbox_id, 0)



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