from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from harness.runtime.events import RunEventType, make_event, run_context
from harness.runtime.runs.models import (
    RUN_STATUS_CANCELLED,
    RUN_STATUS_ERROR,
    RUN_STATUS_FINISHED,
    RUN_STATUS_RUNNING,
    RunHandle,
)
from harness.runtime.serialization import (
    MAX_MESSAGE_PREVIEW,
    as_list,
    convert_messages,
    extract_text_content,
)

"""run 后台执行（runs.worker）

    职责：驱动一次 agent 图 astream 运行，把 values/messages 帧解析成思考链
         事件发布到总线，收尾落库、回填线程元数据、发终端事件。
    流程：设置线程上下文 → 装配本次 run 专属 agent（新建 checkpointer）→
         astream(["values","messages"]) 逐帧解析 → 结束 flush + finalize。
    边界：不管理全局句柄/并发簿记（那是 manager 的事），只管单个 run。

    帧解析要点（与现状一致）：
        - values 帧：对 prints / todos / artifacts 做增量去重后发事件；
        - messages 帧：按 checkpoint_ns 分轮缓冲，轮内出现 tool_call 判为
          思考叙述（thinking_chunk），纯文本判为最终答复（message_chunk）；
        - 收尾 flush 保证最后一轮不漏发。
"""

logger = logging.getLogger(__name__)


async def run_worker(
    handle: RunHandle,
    messages: Any,
    *,
    registry: Any,
    store: Any,
    bus: Any,
    threads: Any,
) -> None:
    """后台执行一次 run：装配专属 agent，驱动 astream 解析并发事件。

    参数：
        handle: 本次 run 的内存句柄
        messages: 输入消息（dict 或 BaseMessage）
        registry: Agent 装配注册表（提供 build_agent / sandbox）
        store: RunStore（runs 表读写）
        bus: EventBus（事件发布）
        threads: ThreadStore（线程元数据回填）
    """
    run_id = handle.run_id
    thread_id = handle.thread_id

    # 热池上下文：装配链（sandbox provider）按线程归属取用确定性沙箱
    from harness.runtime.sandbox import set_runtime_thread_id

    set_runtime_thread_id(thread_id)

    converted = convert_messages(messages)

    handle.status = RUN_STATUS_RUNNING
    await store.update(run_id, fields={"status": RUN_STATUS_RUNNING, "started_at": time.time()})

    final_values: dict[str, Any] | None = None
    agent: Any = None
    checkpointer: Any = None
    live_sandbox: Any = None
    # 装配阶段：失败直接以 error 收尾
    try:
        from harness.memory.short_term import create_checkpointer

        checkpointer = await create_checkpointer()
        agent = registry.build_agent(
            model_name=handle.model_name or None,
            thinking_enabled=handle.thinking_enabled,
            checkpointer=checkpointer,
        )
        live_sandbox = getattr(registry, "sandbox", None)
        if live_sandbox is not None and getattr(live_sandbox, "base_url", None):
            try:
                from harness.runtime.sandbox import push_uploads_to_sandbox

                pushed = push_uploads_to_sandbox(
                    live_sandbox,
                    user_id=handle.user_id,
                    thread_id=thread_id,
                )
                if pushed:
                    logger.info("run %s 推送 %d 个上传文件到沙箱容器", run_id, pushed)
            except Exception:  # noqa: BLE001 —— 推送失败不阻断 run
                logger.warning(
                    "run %s 上传文件推送沙箱失败（忽略）", run_id, exc_info=True
                )
    except Exception as exc:  # noqa: BLE001 —— 装配失败归于 run 错误
        await _finalize(
            handle,
            status=RUN_STATUS_ERROR,
            error=f"agent 装配失败: {exc}",
            final_values=None,
            store=store,
            bus=bus,
            threads=threads,
        )
        return

    # 运行阶段：正常/取消/异常分别收尾；finally 里回传产物、沙箱回源
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
                    await _handle_values(handle, chunk, bus=bus)
                elif mode == "messages":
                    await _handle_message_chunk(handle, chunk, bus=bus)
        await _finalize(
            handle,
            status=RUN_STATUS_FINISHED,
            error=None,
            final_values=final_values,
            store=store,
            bus=bus,
            threads=threads,
        )
    except asyncio.CancelledError:
        await _finalize(
            handle,
            status=RUN_STATUS_CANCELLED,
            error=None,
            final_values=None,
            store=store,
            bus=bus,
            threads=threads,
        )
    except Exception as exc:  # noqa: BLE001 —— run 级异常归于错误状态
        logger.exception("run %s 执行失败", run_id)
        await _finalize(
            handle,
            status=RUN_STATUS_ERROR,
            error=str(exc),
            final_values=None,
            store=store,
            bus=bus,
            threads=threads,
        )
    finally:
        # 关闭本次 run 专属 checkpointer 连接
        if checkpointer is not None:
            try:
                await checkpointer.conn.close()
            except Exception:  # noqa: BLE001 —— 关闭失败不阻断
                pass
        # 容器产物回传宿主 outputs
        try:
            live = getattr(registry, "sandbox", None)
            if live is not None and getattr(live, "base_url", None):
                pulled = await pull_run_outputs(
                    live, user_id=handle.user_id, thread_id=thread_id
                )
                if pulled:
                    logger.info("run %s 容器产物已回传 %d 个文件到宿主", run_id, pulled)
        except Exception:  # noqa: BLE001 —— 回传失败不阻断收尾
            logger.warning("run %s 容器产物回传失败（忽略）", run_id, exc_info=True)

        # 热池回源：run 结束把沙箱保活入池（借用场景以实际实例 id 为准）
        try:
            from harness.runtime.sandbox import release_app_sandbox

            live_final = getattr(registry, "sandbox", None)
            release_app_sandbox(
                sandbox_id=(
                    str(live_final.id) if live_final is not None else None
                )
            )
        except Exception:  # noqa: BLE001 —— 回源失败不阻断收尾
            logger.warning("run %s 沙箱回源热池失败（忽略）", run_id, exc_info=True)


async def _handle_values(handle: RunHandle, values: dict[str, Any], *, bus: Any) -> None:
    """解析一个 values 帧：对 prints / todos / artifacts 发增量事件。

    参数：
        handle: run 句柄（携带增量游标）
        values: 全量 state 快照
        bus: 事件总线
    """
    run_id = handle.run_id
    thread_id = handle.thread_id

    # 切到超步边界，先冲刷未发的模型轮次
    await _flush_msg_round(handle, bus=bus)

    prints = as_list(values.get("prints"))
    # prints 只发新增片段（增量游标）
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
    todos = as_list(values.get("todos"))
    # todos 用指纹去重，内容变化才发全量
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
    artifacts = as_list(values.get("artifacts"))
    # artifacts 只发新增路径（增量游标）
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


async def _handle_message_chunk(handle: RunHandle, chunk: Any, *, bus: Any) -> None:
    """解析一个 messages 帧：按「模型轮次」缓冲，轮结束才定流向。

    参数：
        handle: run 句柄（携带轮次缓冲）
        chunk: (message_chunk, metadata) 元组或单条消息
        bus: 事件总线

    帧是 (message_chunk, metadata) 元组。DeepSeek 这类 Chat 模型在一轮工具
    调用里，叙述文本（"让我先搜索…"）先于 tool_call_chunks 到达；若按单帧
    判定，叙述会被误当最终答复。因此这里按 checkpoint_ns 分轮累积：
      - 轮内出现 tool_call_chunks / tool_calls → 整轮是思考叙述；
      - 轮内只有文本 → 最终答复。
    轮边界由超步（values 帧）或 ns 变化触发冲刷。
    """
    meta: dict[str, Any] = {}
    if isinstance(chunk, tuple) and chunk:
        message, maybe_meta = chunk[0], chunk[1] if len(chunk) > 1 else {}
        if isinstance(maybe_meta, dict):
            meta = maybe_meta
        chunk = message
    # 只关心模型增量消息
    if getattr(chunk, "type", "") not in ("ai", "AIMessageChunk"):
        return
    # 只统计 model 节点的输出（tools / 标题等内部节点不参与）
    node = str(meta.get("langgraph_node") or "")
    if node and node != "model":
        return
    text = extract_text_content(getattr(chunk, "content", ""))
    has_tools = bool(getattr(chunk, "tool_call_chunks", None)) or bool(
        getattr(chunk, "tool_calls", None)
    )
    ns = str(meta.get("langgraph_checkpoint_ns") or "")
    # ns 变化说明进入新的一超步，先冲刷上一轮
    if handle._msg_round_ns is not None and ns and ns != handle._msg_round_ns:
        await _flush_msg_round(handle, bus=bus)
    if ns:
        if handle._msg_round_ns is None:
            handle._msg_round_ns = ns
    else:
        if handle._msg_round_ns is None:
            handle._msg_round_ns = "round-%d" % len(handle._msg_round_text)
    # 记录本轮是否有工具调用与文本
    if has_tools:
        handle._msg_round_has_tools = True
    if text:
        handle._msg_round_text.append(text)


async def _flush_msg_round(handle: RunHandle, *, bus: Any) -> None:
    """冲刷当前模型轮次：按是否有工具调用定流向（思考链 / 最终答复）。

    参数：
        handle: run 句柄
        bus: 事件总线
    """
    if not handle._msg_round_text:
        handle._msg_round_ns = None
        handle._msg_round_has_tools = False
        return
    text = "".join(handle._msg_round_text)
    is_thinking = handle._msg_round_has_tools
    event_type = (
        RunEventType.THINKING_CHUNK if is_thinking else RunEventType.MESSAGE_CHUNK
    )
    # 最终答复文本累积，供收尾生成预览
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
    await bus.publish(
        make_event(
            event_type,
            run_id=handle.run_id,
            thread_id=handle.thread_id,
            payload={"text": payload_text},
        )
    )
    # 轮次缓冲复位
    handle._msg_round_ns = None
    handle._msg_round_text = []
    handle._msg_round_has_tools = False


async def _finalize(
    handle: RunHandle,
    *,
    status: str,
    error: str | None,
    final_values: dict[str, Any] | None,
    store: Any,
    bus: Any,
    threads: Any,
) -> None:
    """run 收尾：落库、回填线程元数据、发终端事件并结束订阅。

    参数：
        handle: run 句柄
        status: 终态（finished / cancelled / error）
        error: 错误信息（正常为 None）
        final_values: 最后一帧 state（用于统计消息数/产物/标题）
        store: RunStore
        bus: EventBus
        threads: ThreadStore

    说明：幂等（handle.finalized 保护）；终端事件必达后 publish_end 结束订阅。
         全局句柄/并发簿记由 manager 在任务结束时清理，不在此处。
    """
    if handle.finalized:
        return
    handle.finalized = True
    handle.status = status
    # 先把未冲刷的最后一轮发出
    await _flush_msg_round(handle, bus=bus)
    run_id = handle.run_id
    thread_id = handle.thread_id
    finished_at = time.time()

    message_count = 0
    artifacts: list[str] = []
    title: str | None = None
    if final_values:
        messages = as_list(final_values.get("messages"))
        message_count = len(messages)
        artifacts = [str(a) for a in as_list(final_values.get("artifacts"))]
        # 会话标题中间件生成的标题（graph state.title），收尾回填线程元数据
        generated = final_values.get("title")
        if isinstance(generated, str) and generated.strip():
            title = generated.strip()
    ai_text = "".join(handle._ai_chunks)
    preview = ai_text[:MAX_MESSAGE_PREVIEW] or handle.input_preview
    await store.update(
        run_id,
        fields={
            "status": status,
            "error": error,
            "artifacts": json.dumps(artifacts, ensure_ascii=False),
            "message_count": message_count,
            "finished_at": finished_at,
        },
    )
    # 回填线程元数据（失败不阻断收尾）
    try:
        meta = threads.get(user_id=handle.user_id, thread_id=thread_id)
        if meta is not None:
            update_fields: dict[str, Any] = {
                "message_count": meta.message_count + message_count,
                "last_message_preview": preview,
                "status": "idle",
            }
            # 仅当线程仍是默认标题时才回填自动标题，避免覆盖用户手动重命名
            if (
                title
                and (not meta.title or meta.title.strip() in ("", "新会话", "新对话"))
            ):
                update_fields["title"] = title
            threads.update(
                user_id=handle.user_id,
                thread_id=thread_id,
                **update_fields,
            )
    except Exception as exc:  # noqa: BLE001 —— 线程元数据回填失败不阻断收尾
        logger.warning("run %s 线程元数据回填失败: %s", run_id, exc)
    # 发终端事件（error 或 finished），随后 END 哨兵结束订阅
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


async def pull_run_outputs(sandbox: Any, *, user_id: str, thread_id: str) -> int:
    """把真实沙箱容器 outputs 目录回传到当前线程宿主的 outputs 目录。

    参数：
        sandbox: 沙箱实例
        user_id: 用户
        thread_id: 线程

    返回：
        回传的文件数量（0 = 无沙箱 / 非真实容器 / 容器无产物）

    说明：仅对真实 AioSandbox 生效（具备 base_url / exec_command）；
        FakeSandbox 或 None 直接返回 0。to_thread 异步执行，避免阻塞事件循环。
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

        pulled = await asyncio.to_thread(pull_container_outputs, sandbox, str(dest))
        return int(pulled or 0)
    except Exception:  # noqa: BLE001 —— 交给调用方（run_worker finally）决定
        raise
