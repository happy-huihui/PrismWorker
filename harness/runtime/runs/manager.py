from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Sequence

from harness.runtime.events import RunEventType, make_event
from harness.runtime.runs.models import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_ERROR,
    RUN_STATUS_FINISHED,
    RUN_STATUS_PENDING,
    RUN_STATUS_RUNNING,
    RunConflictError,
    RunHandle,
    RunNotFoundError,
    RunRecord,
)
from harness.runtime.runs.store import RunStore, row_to_record
from harness.runtime.runs.worker import run_worker
from harness.runtime.serialization import messages_preview
from harness.runtime.sse_stream import get_event_bus
from harness.runtime.threads_data import get_thread_store

"""run 编排管理（runs.manager）

    职责：一次 agent run 的对外编排入口——创建 / 取消 / 查询 / 等待，并做
         同线程并发互斥；把真正的后台执行交给 worker.run_worker。
    流程：create_run 校验并发冲突 → 落 pending 记录 + 发 run_started/run_meta
         → 后台启动 run_worker；run_worker 收尾后由本类清理活动句柄与并发槽。
    依赖：全部构造注入（registry / bus / threads / store），runtime 不自己 new
         （组装由 harness.runtime.assembly 负责）。

    输出数据示例（create_run 返回 RunHandle 关键公开字段）：
        {run_id:"9f3a…", thread_id:"t-1", user_id:"u", status:"pending",
         model_name:"deepseek-v4-pro", input_preview:"帮我查天气", created_at:1.7e9}
"""

logger = logging.getLogger(__name__)


def _thinking_degraded(model_name: str | None, thinking_enabled: bool) -> bool:
    """该次 run 是否“请求了思考但模型不支持”（与工厂的降级判定同源）。

    参数：
        model_name: 配置里的模型名（None = 激活模型）
        thinking_enabled: 用户是否开了思考

    返回：
        True 表示已降级（工厂会忽略思考开关继续跑）；能力查不到时按 False 处理
    """
    if not thinking_enabled:
        return False
    try:
        from harness.models.capabilities import supports_thinking

        return not bool(supports_thinking(model_name))
    except Exception:  # noqa: BLE001 —— 能力探测失败不影响 run，仅少一个提示
        return False


def _route_model(
    messages: Sequence[Any],
    *,
    model_name: str | None,
    thinking_enabled: bool,
) -> tuple[str | None, dict[str, Any]]:
    """用动态路由把「未指定的模型」解析成具体模型名。

    参数：
        messages: 本轮输入消息（路由据此判断意图）
        model_name: 调用方显式指定的模型名（非空则路由会尊重它）
        thinking_enabled: 是否开了深度思考

    返回：
        (最终模型名, 决策元信息 dict)。决策元信息进 run_meta，供前端展示
        「已自动选择 X（因为 Y）」。

    降级：路由模块自身抛错时不影响 run，退回调用方给的名字
        （即改造前的行为），只记一条 warning。
    """
    try:
        from harness.models.routing import get_model_router

        decision = get_model_router().decide(
            messages,
            thinking_enabled=thinking_enabled,
            explicit_model=model_name,
        )
        if decision.model_name != model_name:
            logger.info(
                "模型路由：%s -> %s（%s）",
                model_name or "auto",
                decision.model_name or "auto",
                decision.reason,
            )
        return decision.model_name, decision.to_meta()
    except Exception:  # noqa: BLE001 —— 路由失败不阻断 run，退回原始行为
        logger.warning("模型路由决策失败，使用调用方指定的模型", exc_info=True)
        return model_name, {
            "model_name": model_name or "",
            "source": "fallback",
            "reason": "模型路由不可用，使用调用方指定的模型",
            "matched_keyword": None,
            "escalated": False,
        }


class RunManager:
    """run 编排管理：创建 / 执行 / 取消 / 查询一次 agent run。"""

    def __init__(
        self,
        *,
        registry: Any,
        threads: Any | None = None,
        bus: Any | None = None,
        store: RunStore | None = None,
    ) -> None:
        """初始化。

        参数：
            registry: Agent 装配注册表（必须注入）
            threads: ThreadStore；None 用进程级单例
            bus: EventBus；None 用进程级单例
            store: RunStore；None 按默认库路径构造
        """
        # 装配注册表必须注入（不再自举懒建）
        self._registry = registry
        self._threads = threads or get_thread_store()
        self._bus = bus or get_event_bus()
        self._store = store or RunStore()
        # 活动句柄 run_id -> handle；并发槽 thread_id -> run_id
        self._handles: dict[str, RunHandle] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()
        self._started = False


    async def start(self) -> None:
        """启动服务：建 runs 表（与 checkpointer 同库，独立连接）。

        异常：
            registry 未注入时抛 RuntimeError
        """
        if self._registry is None:
            raise RuntimeError("RunManager 需要注入 registry（由 assembly 组装）")
        await self._store.connect()
        self._started = True

    async def close(self) -> None:
        """关闭服务：取消在跑任务、关闭数据库连接、回收真实沙箱容器。"""
        # 先取消所有未完成任务
        for handle in list(self._handles.values()):
            if handle.task and not handle.task.done():
                handle.task.cancel()
        # 再回收任务（吞掉被取消带来的异常）
        for handle in list(self._handles.values()):
            if handle.task and not handle.task.done():
                try:
                    await handle.task
                except BaseException:  # noqa: BLE001 —— 关闭时吞掉任务异常
                    pass
        await self._store.close()
        self._started = False
        self._handles.clear()
        self._active.clear()
        from harness.runtime.sandbox import stop_app_sandbox

        stop_app_sandbox()


    async def create_run(
        self,
        *,
        user_id: str,
        thread_id: str,
        messages: Sequence[Any],
        model_name: str | None = None,
        thinking_enabled: bool = False,
        auto_create_thread: bool = True,
    ) -> RunHandle:
        """创建一个 run 并后台启动执行（返回句柄，不等结果）。

        参数：
            user_id: 用户
            thread_id: 线程
            messages: 输入消息
            model_name: 模型名（None 用激活模型）
            thinking_enabled: 是否思考模式
            auto_create_thread: 线程不存在时是否自动建元数据

        返回：
            新建的 RunHandle

        异常：
            同线程已有未终结 run → RunConflictError；
            线程不存在且不自动建 → RunNotFoundError
        """
        # 保证库已就绪（未 start 也能安全落库）
        if not self._store.connected:
            await self._store.connect()

        # 动态路由：调用方没指定模型时按规则自动挑（默认 flash，命中关键词升 pro）。
        # 决策原因随 run_meta 下发，前端思考链据此展示「已自动选择 X」。
        resolved_model, routing_meta = _route_model(
            messages,
            model_name=model_name,
            thinking_enabled=thinking_enabled,
        )

        run_id = uuid.uuid4().hex
        created_at = time.time()
        preview = messages_preview(messages)
        # 线程元数据：缺省按开关自动建或报错
        existing = self._threads.get(user_id=user_id, thread_id=thread_id)
        if existing is None:
            if not auto_create_thread:
                raise RunNotFoundError(f"线程不存在: {thread_id}")
            self._threads.create(user_id=user_id, thread_id=thread_id)
        # 并发互斥：同线程同时只允许一个未终结 run
        with self._lock:
            active_run = self._active.get(thread_id)
            if active_run is not None:
                raise RunConflictError(thread_id, active_run)
            self._active[thread_id] = run_id
        handle = RunHandle(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            model_name=resolved_model or "",
            thinking_enabled=thinking_enabled,
            input_preview=preview,
            created_at=created_at,
        )
        self._handles[run_id] = handle
        await self._store.insert(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            status=RUN_STATUS_PENDING,
            model_name=resolved_model or "",
            input_preview=preview,
            created_at=created_at,
        )
        # 开跑先发 run_started 与 run_meta（事件会被总线录制，订阅晚一步也能回放拿到）
        await self._bus.publish(
            make_event(
                RunEventType.RUN_STARTED,
                run_id=run_id,
                thread_id=thread_id,
                payload={"run_id": run_id, "thread_id": thread_id},
            )
        )
        await self._bus.publish(
            make_event(
                RunEventType.RUN_META,
                run_id=run_id,
                thread_id=thread_id,
                payload={
                    "model_name": resolved_model or "auto",
                    "thinking_enabled": bool(thinking_enabled),
                    # 降级标记：用户开了思考但模型不支持（工厂已静默忽略），
                    # 前端据此在思考链里说明，不拿一行错误打断会话
                    "thinking_degraded": _thinking_degraded(resolved_model, thinking_enabled),
                    # 路由决策：模型是怎么被选出来的（含原因），前端展示用
                    "routing": routing_meta,
                },
            )
        )
        # 后台执行；任务结束后清理句柄与并发槽
        handle.task = asyncio.get_running_loop().create_task(
            self._run_and_cleanup(handle, list(messages))
        )
        return handle

    async def _run_and_cleanup(self, handle: RunHandle, messages: Sequence[Any]) -> None:
        """执行 worker 并做簿记收尾（清 active / 摘 handle）。

        参数：
            handle: run 句柄
            messages: 输入消息
        """
        try:
            await run_worker(
                handle,
                messages,
                registry=self._registry,
                store=self._store,
                bus=self._bus,
                threads=self._threads,
            )
        finally:
            # worker 收尾后清并发槽与句柄（与旧 _finish 末尾等价）
            self._handles.pop(handle.run_id, None)
            with self._lock:
                if self._active.get(handle.thread_id) == handle.run_id:
                    self._active.pop(handle.thread_id, None)


    async def _record_from_handle(self, handle: RunHandle) -> RunRecord:
        """活动句柄 → 记录。

        参数：
            handle: run 句柄

        返回：
            运行中走内存快路径；已终态回读库拿完整字段
        """
        if handle.status in (RUN_STATUS_FINISHED, RUN_STATUS_CANCELLED, RUN_STATUS_ERROR):
            if self._store.connected:
                row = await self._store.fetch_row(handle.run_id)
                if row is not None:
                    return row_to_record(row)
        return RunRecord(
            run_id=handle.run_id,
            thread_id=handle.thread_id,
            user_id=handle.user_id,
            status=handle.status,
            model_name=handle.model_name or "",
            input_preview=handle.input_preview,
            created_at=handle.created_at,
        )

    async def cancel_run(self, run_id: str) -> RunRecord:
        """取消一个 run（未结束才有意义），返回最终记录。

        参数：
            run_id: 目标 run

        返回：
            最终 RunRecord

        异常：
            run 既不在内存也不在库 → RunNotFoundError
        """
        handle = self._handles.get(run_id)
        if handle is not None:
            # 非活动态直接等收尾
            if handle.status not in (RUN_STATUS_PENDING, RUN_STATUS_RUNNING):
                return await self.wait_for_run(run_id, timeout=10)
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()
            return await self.wait_for_run(run_id, timeout=10)
        record = await self.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    async def get_run(self, run_id: str) -> RunRecord | None:
        """按 run_id 查询记录（活动句柄优先，否则查库）。

        参数：
            run_id: 目标 run

        返回：
            RunRecord 或 None
        """
        handle = self._handles.get(run_id)
        if handle is not None:
            return await self._record_from_handle(handle)
        if not self._store.connected:
            return None
        row = await self._store.fetch_row(run_id)
        return row_to_record(row) if row is not None else None

    async def list_runs(
        self,
        *,
        user_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        """列出 run 记录（可按用户 / 线程过滤）。

        参数：
            user_id / thread_id: 过滤条件
            limit: 最多条数

        返回：
            内存活动句柄（最新在前）+ 库中已完成记录（补足 limit）
        """
        records: list[RunRecord] = []
        for handle in reversed(list(self._handles.values())):
            if user_id is not None and handle.user_id != user_id:
                continue
            if thread_id is not None and handle.thread_id != thread_id:
                continue
            records.append(await self._record_from_handle(handle))
            if len(records) >= limit:
                break
        if self._store.connected and len(records) < limit:
            rows = await self._store.fetch_rows(
                user_id=user_id,
                thread_id=thread_id,
                limit=limit - len(records),
            )
            seen = {r.run_id for r in records}
            records.extend(
                row_to_record(row) for row in rows if row["run_id"] not in seen
            )
        return records[:limit]

    async def get_thread_chains(
        self, *, user_id: str, thread_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """取某线程已落库 run 的思考链事件（创建时间正序，供前端历史回放）。

        参数：
            user_id / thread_id: 归属
            limit: 最多条数

        返回：
            [{run_id, status, model_name, started_at, finished_at, events:[...]}]；
            未收尾的 run（事件尚未落库）不在其中，由前端实时流承担。
        """
        import json

        if not self._store.connected:
            return []
        rows = await self._store.fetch_chains(
            user_id=user_id, thread_id=thread_id, limit=limit
        )
        chains: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                events = json.loads(item.get("events") or "[]")
            except (json.JSONDecodeError, TypeError):
                events = []
            chains.append(
                {
                    "run_id": item["run_id"],
                    "status": item["status"],
                    "model_name": item.get("model_name") or "",
                    "started_at": item.get("started_at"),
                    "finished_at": item.get("finished_at"),
                    "events": events if isinstance(events, list) else [],
                }
            )
        return chains

    @staticmethod
    async def _await_task_quiet(task: Any) -> None:
        """等待任务结束，忽略任务被取消带来的 CancelledError。

        参数：
            task: 待等待的 asyncio.Task

        说明：await 一个已取消任务会在等待方抛 CancelledError，这里吞掉
            （任务收尾由 worker 的 except CancelledError 分支完成）。
        """
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def wait_for_run(self, run_id: str, timeout: float | None = None) -> RunRecord:
        """等待 run 结束并返回最终记录（超时抛 asyncio.TimeoutError）。

        参数：
            run_id: 目标 run
            timeout: 超时秒数；None 不限

        返回：
            最终 RunRecord

        异常：
            超时抛 asyncio.TimeoutError；找不到抛 RunNotFoundError
        """
        handle = self._handles.get(run_id)
        if handle is not None and handle.task is not None:
            if timeout is None:
                await self._await_task_quiet(handle.task)
            else:
                await asyncio.wait_for(self._await_task_quiet(handle.task), timeout)
        record = await self.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record
