"""app 层真实沙箱运行时（sandbox_runtime）——热池化的懒启动 + 回源复用。

阶段目标（热池版）：
    把 harness 进程级 SandboxManager（含 Warm Pool）接入 app 层 run 装配链：
    - 首用懒启动：首个 run 才启动容器，进程空闲不占资源；
    - 确定性沙箱 id：按 thread_id 派生（t-{hash}），同线程后续 run 直接
      复用同一容器（活跃缓存或热池提升，零冷启动）；
    - 回源热池：run 结束调用 release_app_sandbox 把容器保活回源，
      idle_timeout 后由守护线程自动销毁（热池小容量时共享回退，行为与
      旧版进程级共享容器一致）；
    - 显式 stop 回收：服务关闭（run_service.close）时统一销毁全部容器。

线程上下文：run 是独立 asyncio.Task，用 ContextVar 传递当前 thread_id，
装配链（agent_registry → sandbox provider）无需改签名即可取到线程归属，
并保证并发 run 各自拿到自己的容器（容量闸门内）。

与 harness 的协作点：
    - get_sandbox_manager(config) 是 harness 进程级单例；本模块只做
      「thread_id → 确定性 sandbox_id → manager.start/release」的薄封装；
    - 子代理 / view_image 中间件按 sandbox_id 从同一个管理器取回容器，
      保证全链路同一个实例。

对外 API：
    set_runtime_thread_id / get_runtime_thread_id  线程上下文（run 装配前设置）
    get_app_sandbox()          -> AioSandbox | None  按线程取用沙箱（懒启动）
    release_app_sandbox([id])  -> None                run 结束回源热池（幂等）
    stop_app_sandbox()         -> None                销毁全部容器（幂等）
    push_uploads_to_sandbox(...) -> int               上传目录推送（不变）
"""

from __future__ import annotations

import hashlib
import logging
import threading
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

# 当前 run 的线程归属（asyncio.create_task 自动复制 context，天然隔离并发）
_THREAD_ID_CONTEXT: ContextVar[str | None] = ContextVar(
    "runtime_thread_id", default=None
)

_sandbox_failed: bool = False
_sandbox_lock = threading.Lock()

# 未指定线程时的共享沙箱 id（旧版「进程级单容器」语义的兜底）
_SHARED_SANDBOX_ID = "app-shared"


def set_runtime_thread_id(thread_id: str | None) -> None:
    """设置当前任务上下文中的线程归属（run 装配前调用）。"""
    _THREAD_ID_CONTEXT.set(thread_id)


def get_runtime_thread_id() -> str | None:
    """读取当前任务上下文的线程归属。"""
    return _THREAD_ID_CONTEXT.get()


def derive_sandbox_id(thread_id: str | None) -> str:
    """把线程 id 派生为确定性沙箱 id（t-{sha256 摘要 12 位}）。

    同线程永远命中同 id → 活跃/热池复用；不同线程不同 id → 各自容器。
    """
    if not thread_id:
        return _SHARED_SANDBOX_ID
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:12]
    return f"t-{digest}"


def get_app_sandbox() -> Any | None:
    """返回当前线程所属的真实沙箱实例（懒启动；复用优先）。

    - 未配置 sandbox / 启动失败 → 返回 None（进程内不再重试）；
    - 同线程重复调用 → 活跃缓存直接命中；
    - 热池中有同 id 容器 → 提升复用（零冷启动）。
    """
    global _sandbox_failed
    if _sandbox_failed:
        return None
    try:
        from harness.sandbox.lifecycle import get_sandbox_manager

        from app.core.config import get_app_config

        config = get_app_config().sandbox
        if config is None:
            logger.info("未配置 sandbox，app 层运行不带沙箱工具")
            _sandbox_failed = True
            return None
        manager = get_sandbox_manager(config)
        sandbox_id = derive_sandbox_id(get_runtime_thread_id())
        sbx = manager.start(sandbox_id=sandbox_id)
        if get_runtime_thread_id():
            logger.debug(
                "thread=%s 取用沙箱 %s（active=%d warm=%d）",
                get_runtime_thread_id()[:12],
                sbx.id,
                len(manager.available()),
                manager.warm_count,
            )
        return sbx
    except Exception as exc:  # noqa: BLE001 —— 启动失败安全降级
        _sandbox_failed = True
        logger.warning("真实沙箱启动失败，本次进程不带沙箱工具: %s", exc)
        return None


def release_app_sandbox(sandbox_id: str | None = None) -> None:
    """run 结束后把沙箱回源热池（容器保活待复用；幂等）。

    Args:
        sandbox_id: 实际使用的沙箱实例 id（共享回退时以实际容器 id 为准）；
            None 时按当前线程归属推导。
    """
    if _sandbox_failed:
        return
    try:
        from harness.sandbox.lifecycle import get_sandbox_manager

        from app.core.config import get_app_config

        config = get_app_config().sandbox
        if config is None:
            return
        manager = get_sandbox_manager(config)
        if sandbox_id is None:
            sandbox_id = derive_sandbox_id(get_runtime_thread_id())
        manager.release(sandbox_id)
    except Exception as exc:  # noqa: BLE001 —— 回源失败不阻断收尾
        logger.warning("沙箱回源热池失败（忽略）: %s", exc)


def stop_app_sandbox() -> None:
    """销毁当前进程管理的全部沙箱容器（幂等；服务关闭时调用）。"""
    try:
        from harness.sandbox.lifecycle import get_sandbox_manager

        from app.core.config import get_app_config

        config = get_app_config().sandbox
        if config is None:
            return
        get_sandbox_manager(config).stop()
    except Exception as exc:  # noqa: BLE001 —— 回收失败不阻断关闭流程
        logger.warning("真实沙箱回收失败（忽略）: %s", exc)


def push_uploads_to_sandbox(sandbox: Any, *, user_id: str, thread_id: str) -> int:
    """把线程 uploads 目录内容推送到沙箱 /mnt/user-data/uploads/（幂等覆盖）。

    背景：沙箱容器无宿主工作区挂载（单容器无法同时挂载多线程目录），容器内
    /mnt/user-data 是自举的空目录；read_file 等容器侧工具看不到宿主 uploads。
    与 _run_worker 结束后的 pull_run_outputs（容器 outputs → 宿主）对称，run
    开始前把宿主 uploads → 容器推送一遍，使「上传 → list/read」闭环可用。

    仅文本/可解码内容以 UTF-8 写入；二进制（图片等）回退为字节原文的
    latin-1 保底（read_file 文本场景不受影响）。返回推送成功的文件数；
    单文件失败仅告警跳过，不阻断 run。
    """
    from harness.config.paths import get_paths

    paths = get_paths()
    udir = paths.sandbox_uploads_dir(thread_id, user_id=user_id)
    if not udir.is_dir():
        return 0
    pushed = 0
    from pathlib import Path

    for f in sorted(udir.iterdir()):
        if not f.is_file():
            continue
        virtual = f"/mnt/user-data/uploads/{f.name}"
        try:
            try:
                content: str = Path(f).read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                content = Path(f).read_text(encoding="latin-1", errors="replace")
            sandbox.write_file(virtual, content)
            pushed += 1
        except Exception as exc:  # noqa: BLE001 —— 单文件失败不阻断
            logger.warning("上传文件推送失败 %s: %s", virtual, exc)
    return pushed