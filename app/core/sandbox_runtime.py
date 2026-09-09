"""app 层真实沙箱运行时（sandbox_runtime）——懒启动 + 显式回收一个复用容器。

阶段14 目标：把 harness 进程级 SandboxManager 接入 app 层 run 装配链，让
build_lead_agent 拿到真实 AioSandbox（注册 6 个沙箱工具），并在初始 state
注入 sandbox_id 供子代理 / 中间件按 id 取回同一容器。

生命周期策略（用户确认）：
    - 首用懒启动：第一个 run 才启动容器，进程空闲不占资源
    - 复用一个容器：进程内仅一台，主代理与子代理共享（本期不做池）
    - 显式 stop 回收：服务关闭（run_service.close）时统一回收

与 harness 的协作点：
    - get_sandbox_manager(config) 是 harness 进程级单例；本模块首次启动时
      用 get_app_config().sandbox 构造，之后子代理 / view_image 中间件按
      sandbox_id 从同一个管理器取回容器，保证全链路同一个实例。
    - 启动失败（无 docker / 镜像缺失 / 端口异常）→ 安全降级：返回 None，
      进程内不再重试（保持沙箱关闭语义，与阶段12 之前一致），并记日志。

对外 API：
    get_app_sandbox()   -> AioSandbox | None   进程级懒启动单例
    stop_app_sandbox()  -> None                显式回收容器（幂等）
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_live_sandbox: Any | None = None
_sandbox_failed: bool = False
_sandbox_lock = threading.Lock()


def get_app_sandbox() -> Any | None:
    """返回进程级真实沙箱实例（首用懒启动；失败/未配置返回 None）。"""
    global _live_sandbox, _sandbox_failed
    if _live_sandbox is not None:
        return _live_sandbox
    if _sandbox_failed:
        return None
    with _sandbox_lock:
        if _live_sandbox is not None:
            return _live_sandbox
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
            sbx = manager.start()
            _live_sandbox = sbx
            logger.info(
                "app 层真实沙箱已就绪: sandbox_id=%s base_url=%s",
                sbx.id,
                sbx.base_url,
            )
        except Exception as exc:  # noqa: BLE001 —— 启动失败安全降级
            _sandbox_failed = True
            logger.warning("真实沙箱启动失败，本次进程不带沙箱工具: %s", exc)
        return _live_sandbox


def stop_app_sandbox() -> None:
    """显式回收当前沙箱容器（幂等；服务关闭时调用）。"""
    global _live_sandbox
    if _live_sandbox is None:
        return
    with _sandbox_lock:
        if _live_sandbox is None:
            return
        try:
            from harness.sandbox.lifecycle import get_sandbox_manager

            get_sandbox_manager().stop(sandbox_id=_live_sandbox)
            logger.info("app 层真实沙箱已回收: %s", _live_sandbox.id)
        except Exception as exc:  # noqa: BLE001 —— 回收失败不阻断关闭流程
            logger.warning("真实沙箱回收失败（忽略）: %s", exc)
        finally:
            _live_sandbox = None


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