from __future__ import annotations

import hashlib
import logging
import threading
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

"""沙箱运行时（sandbox.runtime）

    职责：把 harness 进程级 SandboxManager（含热池）接入 run 装配链，做
         「thread_id → 确定性 sandbox_id → manager.start/release」的薄封装。
    流程：首用懒启动（首个 run 才起容器）；同线程后续 run 复用同一容器
         （活跃缓存或热池提升，零冷启动）；run 结束 release 回源保活，
         idle_timeout 后由守护线程自动销毁；服务关闭 stop 统一回收全部容器。
    线程上下文：run 是独立 asyncio.Task，用 ContextVar 传当前 thread_id，
         装配链无需改签名即可取到线程归属，并保证并发 run 各拿各的容器。

    对外暴露：
        - set_runtime_thread_id / get_runtime_thread_id  线程上下文（装配前设置）
        - derive_sandbox_id       thread_id → 确定性沙箱 id
        - get_app_sandbox()       按线程取用沙箱（懒启动，失败安全降级 None）
        - release_app_sandbox()   run 结束回源热池（幂等）
        - stop_app_sandbox()      销毁全部容器（幂等）
        - push_uploads_to_sandbox(...)  宿主 uploads → 容器推送
"""

# 当前 run 的线程归属（asyncio.create_task 自动复制 context，天然隔离并发）
_THREAD_ID_CONTEXT: ContextVar[str | None] = ContextVar(
    "runtime_thread_id", default=None
)

# 沙箱启动失败标记：一旦失败本进程不再重试
_sandbox_failed: bool = False
_sandbox_lock = threading.Lock()

# 未指定线程时的共享沙箱 id（旧版「进程级单容器」语义的兜底）
_SHARED_SANDBOX_ID = "app-shared"


def set_runtime_thread_id(thread_id: str | None) -> None:
    """设置当前任务上下文中的线程归属（run 装配前调用）。

    参数：
        thread_id: 当前 run 的线程 id
    """
    _THREAD_ID_CONTEXT.set(thread_id)


def get_runtime_thread_id() -> str | None:
    """读取当前任务上下文的线程归属。"""
    return _THREAD_ID_CONTEXT.get()


def derive_sandbox_id(thread_id: str | None) -> str:
    """把线程 id 派生为确定性沙箱 id（t-{sha256 摘要 12 位}）。

    参数：
        thread_id: 线程 id；None 归共享兜底 id

    返回：
        确定性沙箱 id（同线程永远同值 → 活跃/热池复用）
    """
    if not thread_id:
        return _SHARED_SANDBOX_ID
    digest = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:12]
    return f"t-{digest}"


def get_app_sandbox() -> Any | None:
    """返回当前线程所属的真实沙箱实例（懒启动；复用优先）。

    返回：
        - 未配置 sandbox / 启动失败 → None（进程内不再重试）；
        - 同线程重复调用 → 活跃缓存直接命中；
        - 热池中有同 id 容器 → 提升复用（零冷启动）。
    """
    global _sandbox_failed
    # 之前已失败则直接降级
    if _sandbox_failed:
        return None
    try:
        from harness.config.app_config import get_app_config
        from harness.sandbox.lifecycle import get_sandbox_manager

        config = get_app_config().sandbox
        if config is None:
            logger.info("未配置 sandbox，运行时不带沙箱工具")
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

    参数：
        sandbox_id: 实际使用的沙箱实例 id（共享回退时以实际容器 id 为准）；
            None 时按当前线程归属推导。
    """
    if _sandbox_failed:
        return
    try:
        from harness.config.app_config import get_app_config
        from harness.sandbox.lifecycle import get_sandbox_manager

        config = get_app_config().sandbox
        if config is None:
            return
        manager = get_sandbox_manager(config)
        # 未显式给 id 就按当前线程推导
        if sandbox_id is None:
            sandbox_id = derive_sandbox_id(get_runtime_thread_id())
        manager.release(sandbox_id)
    except Exception as exc:  # noqa: BLE001 —— 回源失败不阻断收尾
        logger.warning("沙箱回源热池失败（忽略）: %s", exc)


def stop_app_sandbox() -> None:
    """销毁当前进程管理的全部沙箱容器（幂等；服务关闭时调用）。"""
    try:
        from harness.config.app_config import get_app_config
        from harness.sandbox.lifecycle import get_sandbox_manager

        config = get_app_config().sandbox
        if config is None:
            return
        get_sandbox_manager(config).stop()
    except Exception as exc:  # noqa: BLE001 —— 回收失败不阻断关闭流程
        logger.warning("真实沙箱回收失败（忽略）: %s", exc)


def push_uploads_to_sandbox(sandbox: Any, *, user_id: str, thread_id: str) -> int:
    """把线程 uploads 目录内容推送到沙箱 /mnt/user-data/uploads/（幂等覆盖）。

    参数：
        sandbox: 真实沙箱实例
        user_id: 用户
        thread_id: 线程

    返回：
        推送成功的文件数（无 uploads 目录返回 0）

    背景：容器无宿主工作区挂载，/mnt/user-data 是自举空目录，容器侧工具看不到
         宿主 uploads。与 run 结束后的产物回传对称，开跑前把宿主 uploads 推送
         进容器，使「上传 → list/read」闭环可用。仅文本以 UTF-8 写入，二进制
         以 latin-1 保底；单文件失败仅告警跳过，不阻断 run。
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
            # 优先按 UTF-8 文本读，二进制回退 latin-1 保底
            try:
                content: str = Path(f).read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                content = Path(f).read_text(encoding="latin-1", errors="replace")
            sandbox.write_file(virtual, content)
            pushed += 1
        except Exception as exc:  # noqa: BLE001 —— 单文件失败不阻断
            logger.warning("上传文件推送失败 %s: %s", virtual, exc)
    return pushed
