from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.core.agent_registry import run_context
from app.core.event_bus import get_event_bus
from app.core.events import RunEventType, make_event
from app.core.thread_store import get_thread_store

"""
    run 编排服务（run_service）——app 层的运行引擎。

    贯穿全景：HTTP 网关调用 create_run 提交一次会话请求 → 本服务检查并发
    冲突（同线程同时只允许一个 run）→ 从 AgentRegistry 装配本次 run 专属
    agent（每次 run 独立新建短期记忆 checkpointer，图编译时绑定）→ 后台
    run_worker 驱动 astream 逐帧运行 → 把 values/messages 帧解析成思考链
    事件发布到 EventBus（SSE 推送端订阅）→ 结束收尾把 run 记录落进
    checkpoints.db 同库的 runs 表，并回填线程元数据。

    事件语义：事件本身不落盘（瞬时），持久化交给 runs 表；终端事件
    （run_finished / run_error）由 EventBus 保证必达，之后 publish_end 结束
    订阅者迭代。
"""

RUN_STATUS_PENDING = "pending"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_FINISHED = "finished"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_ERROR = "error"

_MAX_INPUT_PREVIEW = 160
_MAX_MESSAGE_PREVIEW = 120

_RUN_COLUMNS = (
    "run_id",
    "thread_id",
    "user_id",
    "status",
    "model_name",
    "input_preview",
    "error",
    "artifacts",
    "message_count",
    "created_at",
    "started_at",
    "finished_at",
)

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    """一次 run 的持久化记录（runs 表行的映射）。"""

    run_id: str
    thread_id: str
    user_id: str
    status: str
    model_name: str = ""
    input_preview: str = ""
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)
    message_count: int = 0
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class RunHandle:
    """内存中的活动 run 句柄（运行期间的生命周期载体）。"""

    run_id: str
    thread_id: str
    user_id: str
    status: str = RUN_STATUS_PENDING
    model_name: str = ""
    thinking_enabled: bool = False
    input_preview: str = ""
    created_at: float = field(default_factory=time.time)
    task: Any | None = None

    _last_prints: int = 0
    _last_artifacts: int = 0
    _last_todos_fingerprint: str = ""
    _ai_chunks: list[str] = field(default_factory=list)

    _msg_round_ns: str | None = None
    _msg_round_text: list[str] = field(default_factory=list)
    _msg_round_has_tools: bool = False

    finalized: bool = False


class RunConflictError(Exception):
    """同线程已有未终结 run：并发冲突，应返回 409。"""

    def __init__(self, thread_id: str, run_id: str) -> None:
        """初始化；记录正在跑的 run_id 供 API 层提示。"""
        super().__init__(f"线程 {thread_id} 已有进行中的 run: {run_id}")
        self.thread_id = thread_id
        self.run_id = run_id


class RunNotFoundError(Exception):
    """run 不存在。"""


class RunService:
    """run 编排服务：创建 / 执行 / 取消 / 查询一次 agent run。"""

    def __init__(
        self,
        *,
        registry: Any | None = None,
        thread_store: Any | None = None,
        bus: Any | None = None,
        db_path: Any | None = None,
    ) -> None:
        """初始化；各依赖缺省走进程级单例（start 后可用）。"""
        self._registry = registry
        self._threads = thread_store or get_thread_store()
        self._bus = bus
        self._db_path = db_path
        self._conn: Any = None
        self._handles: dict[str, RunHandle] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()
        self._started = False


    async def start(self) -> None:
        """启动服务：建 runs 表（与 checkpointer 同库，用独立连接）。"""
        if self._started and self._conn is not None:
            return
        if self._db_path is None:
            from harness.memory.manager import resolve_memory_paths

            _, db_path = resolve_memory_paths(None)
            self._db_path = db_path
        import aiosqlite

        conn = await aiosqlite.connect(str(self._db_path))
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id        TEXT PRIMARY KEY,
                thread_id     TEXT NOT NULL,
                user_id       TEXT NOT NULL,
                status        TEXT NOT NULL,
                model_name    TEXT NOT NULL DEFAULT '',
                input_preview TEXT NOT NULL DEFAULT '',
                error         TEXT,
                artifacts     TEXT NOT NULL DEFAULT '[]',
                message_count INTEGER NOT NULL DEFAULT 0,
                created_at    REAL NOT NULL,
                started_at    REAL,
                finished_at   REAL
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_thread ON runs(thread_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id)")
        await conn.commit()
        self._conn = conn
        self._started = True
        if self._registry is None:
            from app.core.agent_registry import get_agent_registry
            from app.core.config import get_app_config
            from app.core.sandbox_runtime import get_app_sandbox

            self._registry = get_agent_registry(
                app_config=get_app_config(),
                sandbox_provider=get_app_sandbox,
            )

    async def close(self) -> None:
        """关闭服务：取消在跑任务、关闭数据库连接、回收真实沙箱容器。"""
        for handle in list(self._handles.values()):
            if handle.task and not handle.task.done():
                handle.task.cancel()
        for handle in list(self._handles.values()):
            if handle.task and not handle.task.done():
                try:
                    await handle.task
                except BaseException:  # noqa: BLE001 —— 关闭时吞掉任务异常
                    pass
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
        self._started = False
        self._handles.clear()
        self._active.clear()
        from app.core.sandbox_runtime import stop_app_sandbox

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

        并发控制：同线程已有未终结 run → 抛 RunConflictError。
        thread 不存在时按 auto_create_thread 自动建元数据（不建磁盘目录，
        目录由 harness 首次 run 时创建）或抛 RunNotFoundError。
        """
        run_id = uuid.uuid4().hex
        created_at = time.time()
        preview = _messages_preview(messages)
        existing = self._threads.get(user_id=user_id, thread_id=thread_id)
        if existing is None:
            if not auto_create_thread:
                raise RunNotFoundError(f"线程不存在: {thread_id}")
            self._threads.create(user_id=user_id, thread_id=thread_id)
        with self._lock:
            active_run = self._active.get(thread_id)
            if active_run is not None:
                raise RunConflictError(thread_id, active_run)
            self._active[thread_id] = run_id
        handle = RunHandle(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            model_name=model_name or "",
            thinking_enabled=thinking_enabled,
            input_preview=preview,
            created_at=created_at,
        )
        self._handles[run_id] = handle
        await self._insert_run(
            run_id=run_id,
            thread_id=thread_id,
            user_id=user_id,
            status=RUN_STATUS_PENDING,
            model_name=model_name or "",
            input_preview=preview,
            created_at=created_at,
        )
        bus = self._bus or get_event_bus()
        await bus.publish(
            make_event(
                RunEventType.RUN_STARTED,
                run_id=run_id,
                thread_id=thread_id,
                payload={"run_id": run_id, "thread_id": thread_id},
            )
        )
        await bus.publish(
            make_event(
                RunEventType.RUN_META,
                run_id=run_id,
                thread_id=thread_id,
                payload={"model_name": model_name or "auto"},
            )
        )
        handle.task = asyncio.get_running_loop().create_task(
            self._run_worker(handle, list(messages))
        )
        return handle


    async def _run_worker(self, handle: RunHandle, messages: Sequence[Any]) -> None:
        """后台执行一次 run：装配 run 专属 agent，驱动 astream 解析事件。"""
        run_id = handle.run_id
        thread_id = handle.thread_id

        converted = _convert_messages(messages)

        handle.status = RUN_STATUS_RUNNING
        await self._update_run(
            run_id=run_id,
            fields={"status": RUN_STATUS_RUNNING, "started_at": time.time()},
        )
        if self._conn is None:
            await self.start()

        final_values: dict[str, Any] | None = None
        agent: Any = None
        checkpointer: Any = None
        try:
            from harness.memory.checkpointer import create_checkpointer

            checkpointer = await create_checkpointer()
            agent = self._registry.build_agent(
                model_name=handle.model_name or None,
                thinking_enabled=handle.thinking_enabled,
                checkpointer=checkpointer,
            )
            live_sandbox = getattr(self._registry, "sandbox", None)
            if live_sandbox is not None and getattr(live_sandbox, "base_url", None):
                try:
                    from app.core.sandbox_runtime import push_uploads_to_sandbox

                    pushed = push_uploads_to_sandbox(
                        live_sandbox,
                        user_id=handle.user_id,
                        thread_id=thread_id,
                    )
                    if pushed:
                        logger.info(
                            "run %s 推送 %d 个上传文件到沙箱容器", run_id, pushed
                        )
                except Exception:  # noqa: BLE001 —— 推送失败不阻断 run
                    logger.warning(
                        "run %s 上传文件推送沙箱失败（忽略）", run_id, exc_info=True
                    )
        except Exception as exc:  # noqa: BLE001 —— 装配失败归于 run 错误
            await self._finish(
                handle,
                status=RUN_STATUS_ERROR,
                error=f"agent 装配失败: {exc}",
                final_values=None,
            )
            return

        try:
            with run_context(run_id, thread_id):
                config: dict[str, Any] = {
                    "configurable": {
                        "thread_id": thread_id,
                        "user_id": handle.user_id,
                    }
                }
                inputs: dict[str, Any] = {"messages": converted}
                if live_sandbox is not None:
                    inputs["sandbox"] = {"sandbox_id": str(live_sandbox.id)}
                async for mode, chunk in agent.astream(
                    inputs,
                    config=config,
                    stream_mode=["values", "messages"],
                ):
                    if mode == "values":
                        final_values = chunk
                        await self._handle_values(handle, chunk)
                    elif mode == "messages":
                        await self._handle_message_chunk(handle, chunk)
            await self._finish(
                handle,
                status=RUN_STATUS_FINISHED,
                error=None,
                final_values=final_values,
            )
        except asyncio.CancelledError:
            await self._finish(
                handle,
                status=RUN_STATUS_CANCELLED,
                error=None,
                final_values=None,
            )
        except Exception as exc:  # noqa: BLE001 —— run 级异常归于错误状态
            logger.exception("run %s 执行失败", run_id)
            await self._finish(
                handle,
                status=RUN_STATUS_ERROR,
                error=str(exc),
                final_values=None,
            )
        finally:
            if checkpointer is not None:
                try:
                    await checkpointer.conn.close()
                except Exception:  # noqa: BLE001 —— 关闭失败不阻断
                    pass
            try:
                live = getattr(self._registry, "sandbox", None)
                if live is not None and getattr(live, "base_url", None):
                    pulled = await pull_run_outputs(
                        live, user_id=handle.user_id, thread_id=thread_id
                    )
                    if pulled:
                        logger.info("run %s 容器产物已回传 %d 个文件到宿主", run_id, pulled)
            except Exception:  # noqa: BLE001 —— 回传失败不阻断收尾
                logger.warning("run %s 容器产物回传失败（忽略）", run_id, exc_info=True)

    async def _handle_values(self, handle: RunHandle, values: dict[str, Any]) -> None:
        """解析一个 values 帧：对 prints / todos / artifacts 发增量事件。"""
        run_id = handle.run_id
        thread_id = handle.thread_id
        bus = self._bus or get_event_bus()

        await self._flush_msg_round(handle)

        prints = _as_list(values.get("prints"))
        if len(prints) > handle._last_prints:
            delta = prints[handle._last_prints :]
            handle._last_prints = len(prints)
            await bus.publish(
                make_event(
                    RunEventType.PRINTS,
                    run_id=run_id,
                    thread_id=thread_id,
                    payload={"prints": delta},
                )
            )
        todos = _as_list(values.get("todos"))
        if todos:
            fingerprint = json.dumps(todos, ensure_ascii=False, sort_keys=True)
            if fingerprint != handle._last_todos_fingerprint:
                handle._last_todos_fingerprint = fingerprint
                await bus.publish(
                    make_event(
                        RunEventType.TODOS,
                        run_id=run_id,
                        thread_id=thread_id,
                        payload={"todos": todos},
                    )
                )
        artifacts = _as_list(values.get("artifacts"))
        if len(artifacts) > handle._last_artifacts:
            delta = artifacts[handle._last_artifacts :]
            handle._last_artifacts = len(artifacts)
            await bus.publish(
                make_event(
                    RunEventType.ARTIFACTS,
                    run_id=run_id,
                    thread_id=thread_id,
                    payload={"artifacts": delta},
                )
            )

    async def _handle_message_chunk(self, handle: RunHandle, chunk: Any) -> None:
        """解析一个 messages 帧：按「模型轮次」缓冲，轮结束才定流向。

        帧是 (message_chunk, metadata) 元组。DeepSeek 这类 Chat 模型在一轮工具
        调用里，叙述文本（"让我先搜索…"）先于 tool_call_chunks 到达；若按单帧
        判定，叙述会被误当最终答复。因此这里按 checkpoint_ns 分轮累积：
          - 轮内出现 tool_call_chunks / tool_calls → 整轮是思考叙述
            （tools 节点 / 标题生成等内部节点不参与）；
          - 轮内只有文本 → 最终答复。
        轮边界由超步（values 帧）或 ns 变化触发冲刷。
        """
        meta: dict[str, Any] = {}
        if isinstance(chunk, tuple) and chunk:
            message, maybe_meta = chunk[0], chunk[1] if len(chunk) > 1 else {}
            if isinstance(maybe_meta, dict):
                meta = maybe_meta
            chunk = message
        if getattr(chunk, "type", "") not in ("ai", "AIMessageChunk"):
            return
        node = str(meta.get("langgraph_node") or "")
        if node and node != "model":
            return
        text = _extract_text_content(getattr(chunk, "content", ""))
        has_tools = bool(getattr(chunk, "tool_call_chunks", None)) or bool(
            getattr(chunk, "tool_calls", None)
        )
        ns = str(meta.get("langgraph_checkpoint_ns") or "")
        if handle._msg_round_ns is not None and ns and ns != handle._msg_round_ns:
            await self._flush_msg_round(handle)
        if ns:
            if handle._msg_round_ns is None:
                handle._msg_round_ns = ns
        else:
            if handle._msg_round_ns is None:
                handle._msg_round_ns = "round-%d" % len(handle._msg_round_text)
        if has_tools:
            handle._msg_round_has_tools = True
        if text:
            handle._msg_round_text.append(text)

    async def _flush_msg_round(self, handle: RunHandle) -> None:
        """冲刷当前模型轮次：按是否有工具调用定流向（思考链 / 最终答复）。"""
        if not handle._msg_round_text:
            handle._msg_round_ns = None
            handle._msg_round_has_tools = False
            return
        text = "".join(handle._msg_round_text)
        is_thinking = handle._msg_round_has_tools
        event_type = (
            RunEventType.THINKING_CHUNK if is_thinking else RunEventType.MESSAGE_CHUNK
        )
        if not is_thinking:
            handle._ai_chunks.append(text)
        logger.info(
            "run %s ROUND kind=%s len=%d text=%r",
            handle.run_id,
            "thinking" if is_thinking else "answer",
            len(text),
            text[:60],
        )
        payload_text = f"{text}\n" if is_thinking else text
        await (self._bus or get_event_bus()).publish(
            make_event(
                event_type,
                run_id=handle.run_id,
                thread_id=handle.thread_id,
                payload={"text": payload_text},
            )
        )
        handle._msg_round_ns = None
        handle._msg_round_text = []
        handle._msg_round_has_tools = False


    async def _finish(
        self,
        handle: RunHandle,
        *,
        status: str,
        error: str | None,
        final_values: dict[str, Any] | None,
    ) -> None:
        """run 收尾：落库、回填线程元数据、发终端事件并结束订阅。"""
        if handle.finalized:
            return
        handle.finalized = True
        handle.status = status
        await self._flush_msg_round(handle)
        run_id = handle.run_id
        thread_id = handle.thread_id
        finished_at = time.time()
        bus = self._bus or get_event_bus()

        message_count = 0
        artifacts: list[str] = []
        title: str | None = None
        if final_values:
            messages = _as_list(final_values.get("messages"))
            message_count = len(messages)
            artifacts = [str(a) for a in _as_list(final_values.get("artifacts"))]
            # 会话标题中间件生成的标题（graph state.title），收尾时回填线程元数据
            generated = final_values.get("title")
            if isinstance(generated, str) and generated.strip():
                title = generated.strip()
        ai_text = "".join(handle._ai_chunks)
        preview = ai_text[:_MAX_MESSAGE_PREVIEW] or handle.input_preview
        await self._update_run(
            run_id=run_id,
            fields={
                "status": status,
                "error": error,
                "artifacts": json.dumps(artifacts, ensure_ascii=False),
                "message_count": message_count,
                "finished_at": finished_at,
            },
        )
        try:
            meta = self._threads.get(user_id=handle.user_id, thread_id=thread_id)
            if meta is not None:
                update_fields: dict[str, Any] = {
                    "message_count": meta.message_count + message_count,
                    "last_message_preview": preview,
                    "status": "idle",
                }
                # 仅当线程仍是默认标题时才回填自动生成的标题，避免覆盖用户手动重命名
                if (
                    title
                    and (not meta.title or meta.title.strip() in ("", "新会话", "新对话"))
                ):
                    update_fields["title"] = title
                self._threads.update(
                    user_id=handle.user_id,
                    thread_id=thread_id,
                    **update_fields,
                )
        except Exception as exc:  # noqa: BLE001 —— 线程元数据回填失败不阻断收尾
            logger.warning("run %s 线程元数据回填失败: %s", run_id, exc)
        payload: dict[str, Any] = {
            "status": status,
            "message_count": message_count,
            "artifacts": artifacts,
        }
        if error is not None:
            payload["error"] = error
        if status == RUN_STATUS_ERROR:
            await bus.publish(
                make_event(
                    RunEventType.RUN_ERROR,
                    run_id=run_id,
                    thread_id=thread_id,
                    payload=payload,
                )
            )
        else:
            payload["finished_at"] = finished_at
            await bus.publish(
                make_event(
                    RunEventType.RUN_FINISHED,
                    run_id=run_id,
                    thread_id=thread_id,
                    payload=payload,
                )
            )
        await bus.publish_end(run_id)
        self._handles.pop(run_id, None)
        with self._lock:
            if self._active.get(thread_id) == run_id:
                self._active.pop(thread_id, None)


    async def _record_from_handle(self, handle: RunHandle) -> RunRecord:
        """活动句柄 → 记录。

        句柄处于终态时（收尾已完成、句柄待摘除）回读数据库获得
        完整字段（消息数 / 产物 / 时间戳）；仍运行中则走内存快路径，
        避免每次轮询都查库。
        """
        if handle.status in (RUN_STATUS_FINISHED, RUN_STATUS_CANCELLED, RUN_STATUS_ERROR):
            if self._conn is not None:
                row = await self._fetch_row(handle.run_id)
                if row is not None:
                    return _row_to_record(row)
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
        """取消一个 run（未结束才有意义），返回最终记录。"""
        handle = self._handles.get(run_id)
        if handle is not None:
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
        """按 run_id 查询记录（活动句柄优先，否则查库）。"""
        handle = self._handles.get(run_id)
        if handle is not None:
            return await self._record_from_handle(handle)
        if self._conn is None:
            return None
        row = await self._fetch_row(run_id)
        return _row_to_record(row) if row is not None else None

    async def list_runs(
        self,
        *,
        user_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        """列出 run 记录（可按用户 / 线程过滤）。

        结果 = 内存活动句柄（最新在前）+ 库中已完成记录（补足 limit）。
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
        if self._conn is not None and len(records) < limit:
            rows = await self._fetch_rows(
                user_id=user_id,
                thread_id=thread_id,
                limit=limit - len(records),
            )
            seen = {r.run_id for r in records}
            records.extend(_row_to_record(row) for row in rows if row["run_id"] not in seen)
        return records[:limit]

    @staticmethod
    async def _await_task_quiet(task: Any) -> None:
        """等待任务结束，忽略任务被取消带来的 CancelledError。

        用途：cancel_run / close 里等待"刚被 cancel 的任务"——await 一个
        已取消的任务会在等待方抛 CancelledError，这里把它吞掉（任务的
        收尾工作由 worker 的 except CancelledError 分支完成）。
        """
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def wait_for_run(self, run_id: str, timeout: float | None = None) -> RunRecord:
        """等待 run 结束并返回最终记录（超时抛 asyncio.TimeoutError）。"""
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


    async def _insert_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        status: str,
        model_name: str,
        input_preview: str,
        created_at: float,
    ) -> None:
        """插入一条 pending run 记录。"""
        await self._conn.execute(
            "INSERT INTO runs (run_id, thread_id, user_id, status, model_name, "
            "input_preview, error, artifacts, message_count, created_at, "
            "started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, NULL, '[]', 0, ?, NULL, NULL)",
            (run_id, thread_id, user_id, status, model_name, input_preview, created_at),
        )
        await self._conn.commit()

    async def _update_run(self, run_id: str, *, fields: dict[str, Any]) -> None:
        """按字段更新一条 run 记录（只允许 _RUN_COLUMNS 内字段）。"""
        if not fields:
            return
        safe_fields = {k: v for k, v in fields.items() if k in _RUN_COLUMNS}
        if not safe_fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in safe_fields)
        await self._conn.execute(
            f"UPDATE runs SET {assignments} WHERE run_id = ?",
            (*safe_fields.values(), run_id),
        )
        await self._conn.commit()

    async def _fetch_row(self, run_id: str) -> Any:
        """按 run_id 取一行（sqlite3.Row）。"""
        cursor = await self._conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def _fetch_rows(
        self,
        *,
        user_id: str | None,
        thread_id: str | None,
        limit: int,
    ) -> list[Any]:
        """按条件查询多行（倒序，限量）。"""
        conditions: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if thread_id is not None:
            conditions.append("thread_id = ?")
            params.append(thread_id)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor = await self._conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM runs {where} "
            "ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)



_service: RunService | None = None
_service_lock = threading.Lock()


def get_run_service() -> RunService:
    """返回进程级 RunService 单例（懒构造，线程安全）。"""
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = RunService()
        return _service


def reset_run_service() -> None:
    """清空单例（测试隔离用：下一个 get_run_service 重新构造）。"""
    global _service
    with _service_lock:
        _service = None



async def pull_run_outputs(sandbox: Any, *, user_id: str, thread_id: str) -> int:
    """把真实沙箱容器 outputs 目录回传到当前线程宿主的 outputs 目录。

    仅对真实 AioSandbox 生效（具备 base_url / exec_command）；FakeSandbox
    或无沙箱（None）直接返回 0 跳过。to_thread 异步执行，避免阻塞事件循环；
    宿主目标目录由 get_paths() 定位（与 artifacts 解析同源）。

    Returns:
        回传的文件数量（0 = 无沙箱 / 非真实容器 / 容器无产物）
    """
    if sandbox is None or not getattr(sandbox, "base_url", None):
        return 0
    try:
        from harness.config.paths import get_paths

        dest = get_paths().sandbox_outputs_dir(thread_id, user_id=user_id)
    except Exception:  # noqa: BLE001 —— 路径解析失败按孤立处理
        logger.warning("产物回传：无法定位线程 outputs 目录（user=%s thread=%s）", user_id, thread_id)
        return 0
    try:
        from harness.sandbox.transfer import pull_container_outputs

        return_ = await asyncio.to_thread(pull_container_outputs, sandbox, str(dest))
        return int(return_ or 0)
    except Exception:  # noqa: BLE001 —— 交给调用方（_run_worker 收尾容忍）决定
        raise



def _as_list(value: Any) -> list[Any]:
    """state 通道值 → 列表（兼容 None / 列表 / 元组 / 字符串）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [value]
    return []


def _extract_text_content(content: Any) -> str:
    """消息 content → 纯文本（兼容 str 与 LangChain 内容块列表）。"""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _messages_preview(messages: Sequence[Any]) -> str:
    """取首条用户消息文本作为输入预览（截断）。"""
    for message in messages:
        if isinstance(message, dict):
            content = message.get("content") or ""
        else:
            content = getattr(message, "content", "") or ""
        if isinstance(content, list):
            content = _extract_text_content(content)
        text = str(content).strip()
        if text:
            return text[:_MAX_INPUT_PREVIEW]
    return ""


def _convert_messages(messages: Sequence[Any]) -> list[Any]:
    """把 dict 形式的消息统一转成 BaseMessage（无 langchain 时原样返回）。"""
    if messages and all(hasattr(m, "type") for m in messages):
        return list(messages)
    try:
        from langchain_core.messages import convert_to_messages

        return convert_to_messages(messages)
    except Exception:  # noqa: BLE001 —— 转换失败原样传递
        return list(messages)


def _row_to_record(row: Any) -> RunRecord:
    """DB 行 → RunRecord（兼容 sqlite3.Row）。"""
    item = dict(row)
    try:
        artifacts = json.loads(item.get("artifacts") or "[]")
    except (json.JSONDecodeError, TypeError):
        artifacts = []
    artifact_list: list[str] = [str(a) for a in artifacts] if isinstance(artifacts, list) else []
    return RunRecord(
        run_id=item["run_id"],
        thread_id=item["thread_id"],
        user_id=item["user_id"],
        status=item["status"],
        model_name=item.get("model_name") or "",
        input_preview=item.get("input_preview") or "",
        error=item.get("error"),
        artifacts=artifact_list,
        message_count=int(item.get("message_count") or 0),
        created_at=float(item.get("created_at") or 0.0),
        started_at=float(item["started_at"]) if item.get("started_at") is not None else None,
        finished_at=float(item["finished_at"]) if item.get("finished_at") is not None else None,
    )