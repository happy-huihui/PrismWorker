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
        - set_runtime_user_id / get_runtime_user_id      用户上下文（同上，算挂载路径用）
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

# 当前 run 的用户归属：只用于拼出「本线程 user-data 的宿主路径」当挂载源。
# 为什么要单独一个 ContextVar 而不是复用 runtime 对象：get_app_sandbox() 在
# 装配链上被调用时拿不到 LangGraph 的 runtime，只能靠上下文变量传。
_USER_ID_CONTEXT: ContextVar[str | None] = ContextVar(
    "runtime_user_id", default=None
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


def set_runtime_user_id(user_id: str | None) -> None:
    """设置当前任务上下文中的用户归属（run 装配前调用）。

    参数：
        user_id: 当前 run 的用户 id（多用户隔离目录要用）
    """
    _USER_ID_CONTEXT.set(user_id)


def get_runtime_user_id() -> str | None:
    """读取当前任务上下文的用户归属。"""
    return _USER_ID_CONTEXT.get()


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


def _host_workspace_dir(thread_id: str | None) -> str | None:
    """本线程 user-data 的宿主路径（作为容器 `/mnt/user-data` 的挂载源）。

    为什么必须挂载（2026-09-23 实测缺陷）：
        不挂载时容器里的 `/mnt/user-data` 是**容器本地目录**，宿主在 run 结束前
        看不到模型写出的文件；而 `present_files` 校验的是**宿主**路径，于是必然
        报「文件不存在」→ 产物落盘了却没登记，前端产物栏是空的。
        实测证据：run 20:58:30 开始、21:05:01 结束，宿主上那个 HTML 的落盘时间是
        21:05:02.927（比 run 结束还晚 1.9 秒）—— 正好是 run 收尾回传的那一刻，
        说明中途写入根本没同步到宿主。
        挂载后宿主与容器是同一份文件：登记即可用，run 收尾的回传自然变成 no-op。

    返回：
        宿主绝对路径；拼不出来（无 thread_id / 路径异常）时返回 None，
        此时退回「容器本地目录 + 收尾回传」的旧语义，不会报错。
    """
    if not thread_id:
        return None
    try:
        from harness.config.paths import get_paths

        return str(
            get_paths().sandbox_user_data_dir(thread_id, user_id=get_runtime_user_id())
        )
    except Exception:  # noqa: BLE001 —— 解析失败就不挂载，不阻断沙箱启动
        logger.warning("解析线程挂载目录失败，本次不挂载工作区", exc_info=True)
        return None


def is_workspace_mounted(thread_id: str | None = None) -> bool:
    """本线程的沙箱是否挂载了宿主工作区。

    用途：run 收尾的「容器 outputs → 宿主 outputs」回传，在**已挂载**时是冗余的
    （两边本来就是同一份文件），应当跳过，省掉一次 base64 全量搬运。

    参数：
        thread_id: 线程 id；None 取当前上下文

    返回：
        能拼出挂载目录 → True；否则 False（退回旧的「收尾回传」语义）
    """
    return _host_workspace_dir(thread_id or get_runtime_thread_id()) is not None


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
        thread_id = get_runtime_thread_id()
        sandbox_id = derive_sandbox_id(thread_id)
        # 传入本线程 user-data 宿主路径作为挂载源：容器与宿主共用同一份文件，
        # 这样模型写出的产物在 run 中途就能被 present_files 校验到（见 _host_workspace_dir）。
        sbx = manager.start(
            sandbox_id=sandbox_id,
            host_workspace_dir=_host_workspace_dir(thread_id),
        )
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
