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
    extract_reasoning_content,
    extract_text_content,
)

"""run 后台执行（runs.worker）

    职责：驱动一次 agent 图 astream 运行，把 values/messages 帧解析成思考链
         事件发布到总线，收尾落库、回填线程元数据、发终端事件。
    流程：设置线程上下文 → 装配本次 run 专属 agent（新建 checkpointer）→
         astream(["values","messages"]) 逐帧解析 → 结束 flush + finalize。
    边界：不管理全局句柄/并发簿记（那是 manager 的事），只管单个 run。

    帧解析要点：
        - values 帧：先冲刷未发文本，再对 prints / todos / artifacts 做增量去重后发事件；
        - messages 帧：不再「整轮缓冲后一次发」——模型的思考（reasoning_content）与
          正文都按 token 入缓冲、按时间片/字数节流下发，带 message_id；
        - 定性后置：正文先当答复发出（用户立刻看到字流），同一条消息里一旦出现
          工具调用，补发 message_retract 让前端把这段文本降级成思考叙述；
        - 收尾 flush 保证最后一轮不漏发。
"""

logger = logging.getLogger(__name__)

# 文本增量下发节流：距上次冲刷超过该秒数，或单条缓冲攒够该字数，就发一次。
# 实测模型块间隔 22~32ms，不合并就是每秒 ~40 个 SSE 帧 + 同样多次前端重渲染。
TEXT_FLUSH_INTERVAL = 0.04
# 单条缓冲达到该字数就立即发（长段不能干等时间片）。
# 注意：这个阈值不能太高——values 帧在超步边界会强制冲刷，若阈值过高，
# 模型单轮中途的短增量就会一直等不到时间片（时钟被边界冲刷重置），
# 表现为「攒一大坨才吐一次」。40 字约等于 1~2 个中文句子的长度。
TEXT_FLUSH_CHARS = 40

# 缓冲 kind → 事件类型（reasoning = 真思考，message = 本轮正文）
_TEXT_EVENT_TYPES = {
    "reasoning": RunEventType.REASONING_CHUNK,
    "message": RunEventType.MESSAGE_CHUNK,
}
# 同一批冲刷内的发送顺序：先思考后正文，与模型实际输出顺序一致
_TEXT_KIND_ORDER = ("reasoning", "message")


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

    # 热池上下文：装配链（sandbox provider）按线程归属取用确定性沙箱；
    # user_id 用于拼出「本线程 user-data 的宿主路径」当容器挂载源。
    from harness.runtime.sandbox import set_runtime_thread_id, set_runtime_user_id

    set_runtime_thread_id(thread_id)
    set_runtime_user_id(handle.user_id)

    converted = convert_messages(messages)

    handle.status = RUN_STATUS_RUNNING
    # 墙钟起点：任务清单收尾用它判断「todos_touched_at 是否属于本 run」
    handle._run_started_wall = time.time()
    await store.update(run_id, fields={"status": RUN_STATUS_RUNNING, "started_at": time.time()})

    final_values: dict[str, Any] | None = None
    agent: Any = None
    checkpointer: Any = None
    live_sandbox: Any = None
    # 装配阶段：失败直接以 error 收尾
    try:
        from harness.memory.short_term import create_checkpointer

        checkpointer = await create_checkpointer()

        # 装配移出事件循环：build_agent 内部经 sandbox_provider → SandboxManager.start()
        # 会做 docker subprocess 探测与 wait_for_sandbox_ready 的 time.sleep 轮询
        # （冷启动最长 120s），若在事件循环里直调会冻结整个 FastAPI 服务，
        # 期间任何 HTTP 请求（含新建会话 POST /threads）都无法处理。
        # asyncio.to_thread 会复制 contextvars，runtime 线程归属上下文不受影响。
        def _assemble() -> tuple[Any, Any]:
            assembled = registry.build_agent(
                model_name=handle.model_name or None,
                thinking_enabled=handle.thinking_enabled,
                checkpointer=checkpointer,
            )
            return assembled, getattr(registry, "sandbox", None)

        agent, live_sandbox = await asyncio.to_thread(_assemble)
        if live_sandbox is not None and getattr(live_sandbox, "base_url", None):
            try:
                from harness.runtime.sandbox import push_uploads_to_sandbox

                # 同样是同步 HTTP（base64 逐文件推送），不能占用事件循环
                pushed = await asyncio.to_thread(
                    push_uploads_to_sandbox,
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
            agent=agent,
            config=config,
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
            agent=agent,
            config=config,
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
            agent=agent,
            config=config,
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

    # 切到超步边界，先冲刷未发的模型文本（保证事件与超步顺序对齐）。
    # 这是「边界冲刷」：清空缓冲但不动节流时钟，否则会饿死 messages 帧的节流发送。
    await _flush_text(handle, bus=bus, reset_clock=False)

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
    """解析一个 messages 帧：思考与正文按 token 入缓冲，节流下发（定性后置）。

    参数：
        handle: run 句柄（携带文本待发缓冲与消息定性表）
        chunk: (message_chunk, metadata) 元组或单条消息
        bus: 事件总线

    帧是 (message_chunk, metadata) 元组。为何「先当答复发、事后降级」：
    DeepSeek 这类模型在一轮工具调用里，叙述文本（“让我先搜索…”）先于
    tool_call_chunks 到达，单帧无法预判本轮是否带工具。旧做法是整轮缓冲完了
    再发 → 答复完全不流式。现在先按答复逐 token 下发（用户立即看到字在动），
    本轮的 message_id 上一旦出现工具调用就补发 message_retract，前端把这段
    文本从答复气泡搬进思考步骤（DeerFlow 同款取舍：允许一次轻微跳位）。

    定性键是 **(message_id, round_ns) 而非单纯 message_id**。原因（实测
    2026-09-23，DeepSeek）：同一次 run 内所有轮次复用同一个 message_id，
    若只按 message_id 定性，第一轮的工具调用会把该 id 永久锁成「叙述」，
    导致后续每一轮的正文（包括最终答复）全部被吞——收尾统计出现
    「答复=0字」，前端表现为一个永久空白的答复气泡。按轮次定性后，
    每轮是否算叙述只看该轮自己有没有工具调用。
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
    # 超步变化说明进入新的一轮，先把上一轮未发文本冲刷掉（边界冲刷，不动时钟）
    ns = str(meta.get("langgraph_checkpoint_ns") or "")
    if handle._msg_round_ns is not None and ns and ns != handle._msg_round_ns:
        await _flush_text(handle, bus=bus, reset_clock=False)
    if ns:
        handle._msg_round_ns = ns
    # 消息 id 是前端归并与定性的 key；极端情流缺失时用占位 key 兑底
    message_id = str(getattr(chunk, "id", "") or "") or "-"
    # 轮次键：优先用 checkpoint_ns，缺失时退化为当前记录值，保证同一轮稳定
    round_ns = ns or handle._msg_round_ns or ""
    # 1.模型真实思考（reasoning_content 增量）
    reasoning = extract_reasoning_content(chunk)
    if reasoning:
        _stage_text(handle, "reasoning", message_id, round_ns, reasoning)
    # 2.本轮正文（先当答复流式）
    text = extract_text_content(getattr(chunk, "content", ""))
    if text:
        _stage_text(handle, "message", message_id, round_ns, text)
    # 3.工具调用到达 → 本条消息定性为思考叙述
    has_tools = bool(getattr(chunk, "tool_call_chunks", None)) or bool(
        getattr(chunk, "tool_calls", None)
    )
    if has_tools and (message_id, round_ns) not in handle._msg_retracted:
        handle._msg_retracted.add((message_id, round_ns))
        # 先把这条消息的待发文本冲刷，避免 retract 跑到文本前面造成错位。
        # 注意用边界冲刷：这里清空了本条消息的缓冲，若顺带重置节流时钟，
        # 会让紧随其后的正文帧（同一轮里的最终答复）重新陷入"攒够 40 字才发"。
        await _flush_text(
            handle, bus=bus, message_id=message_id, round_ns=round_ns, reset_clock=False
        )
        await bus.publish(
            make_event(
                RunEventType.MESSAGE_RETRACT,
                run_id=handle.run_id,
                thread_id=handle.thread_id,
                payload={"message_id": message_id},
            )
        )
        # 故意不 return：本帧可能还带着别的待发文本（例如上一轮尾巴），
        # 且要继续维持节流节奏。旧版在这里直接 return，导致该帧的冲刷被跳过。
    # 4.常规路径：按时间片/字数节流冲刷
    await _flush_text_throttled(handle, bus=bus)


def _stage_text(
    handle: RunHandle,
    kind: str,
    message_id: str,
    round_ns: str,
    delta: str,
) -> None:
    """把一段文本增量归入待发缓冲（不直接发，由冲刷控制节奏）。

    参数：
        handle: run 句柄
        kind: reasoning / message
        message_id: 模型消息 id（前端按它归并与定性）
        round_ns: 当前模型轮次的 checkpoint_ns（与 message_id 合成轮次键）
        delta: 本次到达的文本片段
    """
    key = (kind, message_id, round_ns)
    handle._text_pending[key] = handle._text_pending.get(key, "") + delta
    # 正文累计台账：收尾算答复预览时要按它剔除被降级的轮次
    if kind == "message":
        msg_key = (message_id, round_ns)
        handle._msg_text[msg_key] = handle._msg_text.get(msg_key, "") + delta


async def _flush_text(
    handle: RunHandle,
    *,
    bus: Any,
    message_id: str | None = None,
    round_ns: str | None = None,
    reset_clock: bool = True,
) -> None:
    """冲刷文本待发缓冲（全量或只冲指定消息/轮次），每类一个事件。

    参数：
        handle: run 句柄
        bus: 事件总线
        message_id: 只冲这条消息；None 为全部
        round_ns: 只冲这一轮；None 为全部（与 message_id 同时给则取交集）
        reset_clock: 是否顺带把节流时钟拨到现在。

    说明：reset_clock 是修复「答复不流式」的关键参数。
        超步边界（values 帧）、消息定性、run 收尾都会强制冲刷一次，
        若这些边界冲刷也去重置 _text_last_flush，则模型单轮生成期间
        频繁到达的 values 帧会把节流时钟一直拨回原点，
        导致后续 messages 帧永远满足不了「距上次冲刷 ≥ 0.04s」的条件、
        只能等单条攒够 TEXT_FLUSH_CHARS 才发 —— 用户看到的就是
        「静默很久，然后一整坨答复突然出现」。
        因此只有「节流路径」的冲刷才该重置时钟；边界冲刷只清缓冲不动时钟。
    """
    if not handle._text_pending:
        return
    for kind in _TEXT_KIND_ORDER:
        keys = [
            key
            for key in handle._text_pending
            if key[0] == kind
            and (message_id is None or key[1] == message_id)
            and (round_ns is None or key[2] == round_ns)
        ]
        for key in keys:
            text = handle._text_pending.pop(key, "")
            if not text:
                continue
            await bus.publish(
                make_event(
                    _TEXT_EVENT_TYPES[kind],
                    run_id=handle.run_id,
                    thread_id=handle.thread_id,
                    payload={"message_id": key[1], "text": text},
                )
            )
    if reset_clock:
        handle._text_last_flush = time.monotonic()


async def _flush_text_throttled(handle: RunHandle, *, bus: Any) -> None:
    """节流冲刷：时间片到了、或单条缓冲攒得够多，才真发。

    参数：
        handle: run 句柄
        bus: 事件总线

    说明：超步边界 / 消息定性 / run 收尾都走 _flush_text 全量冲刷，所以
        尾巴不会卡在缓冲里；这里只管“流式中途”的发送频率。
        只有本路径会重置节流时钟（见 _flush_text 的 reset_clock 说明）。
    """
    now = time.monotonic()
    if now - handle._text_last_flush < TEXT_FLUSH_INTERVAL:
        # 时间片未到，但单条已攒够字数也要立即发（长段不能干等）
        if not any(len(t) >= TEXT_FLUSH_CHARS for t in handle._text_pending.values()):
            return
    await _flush_text(handle, bus=bus, reset_clock=True)


async def _finalize(
    handle: RunHandle,
    *,
    status: str,
    error: str | None,
    final_values: dict[str, Any] | None,
    store: Any,
    bus: Any,
    threads: Any,
    agent: Any = None,
    config: dict[str, Any] | None = None,
) -> None:
    """run 收尾：落库、任务清单收尾、回填线程元数据、发终端事件并结束订阅。

    参数：
        handle: run 句柄
        status: 终态（finished / cancelled / error）
        error: 错误信息（正常为 None）
        final_values: 最后一帧 state（用于统计消息数/产物/标题）
        store: RunStore
        bus: EventBus
        threads: ThreadStore
        agent: 本 run 的图实例（任务清单收尾要写回 checkpoint；装配失败时为 None）
        config: 本 run 的 langgraph config（aupdate_state 用）

    说明：幂等（handle.finalized 保护）；终端事件必达后 publish_end 结束订阅。
         全局句柄/并发簿记由 manager 在任务结束时清理，不在此处。
    """
    if handle.finalized:
        return
    handle.finalized = True
    handle.status = status
    # 先把未冲刷的最后一轮文本发出（收尾边界冲刷，时钟已无意义）
    await _flush_text(handle, bus=bus, reset_clock=False)
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
    # 答复文本 = 各「消息 + 轮次」正文里「没被降级成叙述」的那部分（按到达顺序拼接）。
    # 键含轮次：同一 message_id 的多轮正文不能混为一谈，只有被降级的那一轮要剔除。
    ai_text = "".join(
        text
        for msg_key, text in handle._msg_text.items()
        if msg_key not in handle._msg_retracted
    )
    logger.info(
        "run %s 收尾 kind=%s 答复=%d字 降级为叙述=%d条 消息=%d条",
        run_id,
        status,
        len(ai_text),
        len(handle._msg_retracted),
        len(handle._msg_text),
    )

    # ── 任务清单收尾（2026-09-26 用户定稿语义）─────────────────────────
    #   · 本轮模型没产出计划（todos_touched_at 未越过本 run 起点）→ 清空：
    #     「新问题没有计划，就不该挂着上一轮的旧清单」。
    #   · 产出过 → 如实收尾：还挂在 in_progress 的条目说明「没做完就结束了」，
    #     落到 cancelled，不伪造 completed。
    #   · 两条路都要：① aupdate_state 写回 checkpoint（下一轮看到干净状态）
    #                ② 发 TODOS 事件（前端即时更新，无需刷新）。
    if agent is not None and config is not None:
        values = final_values
        if values is None:
            # 取消/异常路径没有最后一帧：从 checkpoint 取回状态，
            # 任务清单才能如实收尾（否则只能清空，丢掉「做了一半被取消」的信息）
            try:
                snapshot = await agent.aget_state(config)
                values = snapshot.values if snapshot is not None else None
            except Exception:  # noqa: BLE001 —— 取不到状态就按清空处理
                logger.warning("run %s 收尾取回 checkpoint 状态失败", run_id, exc_info=True)
                values = None
        raw_todos = as_list(values.get("todos")) if values else []
        touched_at = 0.0
        if values:
            raw_touched = values.get("todos_touched_at")
            if isinstance(raw_touched, (int, float)):
                touched_at = float(raw_touched)
        # 本轮模型碰过 todo = 触达时刻晚于本 run 启动（todos_touched_at 持久化在
        # checkpoint 里，上一轮的时间戳必然早于本 run 启动，故该比较能区分两轮）
        if touched_at > handle._run_started_wall:
            final_todos = [
                {
                    **item,
                    "status": (
                        "cancelled"
                        if item.get("status") == "in_progress"
                        else item.get("status")
                    ),
                }
                for item in raw_todos
                if isinstance(item, dict)
            ]
            action = "收尾"
        else:
            final_todos = []
            action = "清空"
        try:
            # as_node 必须显式指定（实测省略会抛 Ambiguous update）；
            # todos 是独立通道，挂到 model 节点只为满足 langgraph 的归属要求
            await agent.aupdate_state(config, {"todos": final_todos}, as_node="model")
            await bus.publish(
                make_event(
                    RunEventType.TODOS,
                    run_id=run_id,
                    thread_id=thread_id,
                    payload={"todos": final_todos},
                )
            )
            logger.info(
                "run %s 任务清单%s：%d 项（模型触达=%s）",
                run_id,
                action,
                len(final_todos),
                touched_at > handle._run_started_wall,
            )
        except Exception:  # noqa: BLE001 —— 清空/写回失败不阻断收尾
            logger.warning("run %s 任务清单收尾失败（忽略）", run_id, exc_info=True)

    preview = ai_text[:MAX_MESSAGE_PREVIEW] or handle.input_preview
    # 取回本 run 录制的思考链事件流（供历史回放）；总线无此能力时降级为空
    drain = getattr(bus, "drain_record", None)
    chain_events = drain(run_id) if callable(drain) else []
    await store.update(
        run_id,
        fields={
            "status": status,
            "error": error,
            "artifacts": json.dumps(artifacts, ensure_ascii=False),
            "events": json.dumps(chain_events, ensure_ascii=False),
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
    # 已挂载宿主工作区时，容器的 /mnt/user-data/outputs 就是下面这个 dest 目录本身，
    # 回传纯属重复搬运（读一遍再原样写回自己）→ 直接跳过。
    from harness.runtime.sandbox import is_workspace_mounted

    if is_workspace_mounted(thread_id):
        logger.debug("沙箱已挂载宿主工作区，跳过产物回传（两边同一份文件）")
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
