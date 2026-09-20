from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import uuid
from collections import OrderedDict
from typing import Any

from harness.memory.config import PrismMemConfig
from harness.memory.processing import (
    detect_signals,
    extract_message_text,
    format_conversation_for_update,
    load_prompt_messages,
)
from harness.memory.storage import (
    MemoryStorage,
    MemoryStorageCorruption,
    create_empty_memory,
    utc_now_iso_z,
)

logger = logging.getLogger(__name__)

"""记忆更新器（extraction.updater）

    职责：把清洗后的对话增量提取为长期记忆——水位线切分、LLM 提取、
         确定性门控应用、乐观写回，以及 fact CRUD / 文档级管理 op。
    流程（update_memory 主干）：
        1. 水位线切分：只喂「上次成功提取之后」的新消息（找不到则全量）；
        2. 组装提示词：当前记忆 + 对话文本 + 信号 hint → chat 模板；
        3. LLM 提取：同步 invoke，解析出更新 JSON；
        4. 门控应用：scope/durability/authority 分类门控 → 置信度阈值 →
           内容去重 → 矛盾移除（依赖替代事实校验）→ 事实上限裁剪；
        5. 乐观写回 + 推进水位线；失败一律返回 False 不推进，下轮自动重喂。
    模型：优先 PrismMemConfig.model，否则复用主模型 create_chat_model(name=None)。
    边界：全程 best-effort，任何异常只记日志，绝不扩散到对话主链路。
"""

# 事实分类三个门控字段（提取元数据，不落库）
_FACT_CLASSIFICATION_FIELDS = ("scope", "durability", "authority")

# 提取输出中六个分区名（user / history）
_USER_SECTIONS = ("workContext", "personalContext", "topOfMind")
_HISTORY_SECTIONS = ("recentMonths", "earlierContext", "longTermBackground")

# 从 LLM 响应里剥离 ```json ... ``` 代码围栏
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_text(content: Any) -> str:
    """提取 LLM 响应纯文本（兼容 str 与多模态块列表）。

    参数：
        content: 模型响应 content

    返回：
        纯文本（列表用换行拼接）
    """
    # 1.字符串直接返回
    if isinstance(content, str):
        return content
    # 2.多模态块：str 段与 text 块都用换行拼起来
    if isinstance(content, list):
        pieces: list[str] = []
        for block in content:
            if isinstance(block, str):
                pieces.append(block)
            elif isinstance(block, dict):
                text_val = block.get("text")
                if isinstance(text_val, str):
                    pieces.append(text_val)
        return "\n".join(pieces)
    # 3.兜底转字符串
    return str(content)


def _normalize_gate_label(value: Any) -> str | None:
    """归一化模型产出的门控标签（去空白小写；非字符串返回 None）。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _fact_scope_gate_reason(fact: dict[str, Any]) -> str | None:
    """返回模型事实被确定性门控拒绝的原因（None = 通过）。

    门控要求：三个分类字段齐全，且 scope=user、durability=durable、
    authority=descriptive 才允许落库（只记「用户级、长期、陈述性」事实）。
    """
    # 1.分类字段缺失即拒
    if any(_normalize_gate_label(fact.get(field)) is None for field in _FACT_CLASSIFICATION_FIELDS):
        return "missing"
    # 2.scope 必须是 user（否则是线程/项目级，不入长期记忆）
    if _normalize_gate_label(fact.get("scope")) != "user":
        return "scope"
    # 3.durability 必须是 durable
    if _normalize_gate_label(fact.get("durability")) != "durable":
        return "durability"
    # 4.authority 必须是 descriptive（陈述，而非指令/臆测）
    if _normalize_gate_label(fact.get("authority")) != "descriptive":
        return "authority"
    return None


def _summary_scope_gate_reason(section_data: dict[str, Any]) -> str | None:
    """摘要分区门控：scope=user 且 authority=descriptive 才可写。"""
    scope = _normalize_gate_label(section_data.get("scope"))
    authority = _normalize_gate_label(section_data.get("authority"))
    # 1.标签缺失
    if scope is None or authority is None:
        return "missing"
    # 2.非用户级
    if scope != "user":
        return "scope"
    # 3.非陈述性
    if authority != "descriptive":
        return "authority"
    return None


def _removal_scope_gate_reason(removal: dict[str, Any]) -> str | None:
    """矛盾移除门控：必须带 scope=user 与非空 reason。"""
    scope = _normalize_gate_label(removal.get("scope"))
    reason = removal.get("reason")
    # 1.scope 缺失或 reason 空
    if scope is None or not isinstance(reason, str) or not reason.strip():
        return "missing"
    # 2.非用户级不允许删
    if scope != "user":
        return "scope"
    return None


def _normalize_fact(fact: Any) -> dict[str, Any] | None:
    """归一化一条模型事实（校验内容/类别/置信度/门控标签）。

    参数：
        fact: 模型产出的原始事实

    返回：
        规整后的 {content, category, confidence, [分类字段]}；非法返回 None
    """
    # 1.必须是 dict 且有非空 content
    if not isinstance(fact, dict):
        return None
    raw_content = fact.get("content")
    if not isinstance(raw_content, str):
        return None
    content = raw_content.strip()
    if not content:
        return None

    # 2.类别缺省归 context
    raw_category = fact.get("category")
    category = raw_category.strip() if isinstance(raw_category, str) and raw_category.strip() else "context"

    # 3.置信度：拒绝 bool，字符串转 float，越界/非有限数拒绝
    raw_confidence = fact.get("confidence", 0.5)
    if isinstance(raw_confidence, bool):
        return None
    if isinstance(raw_confidence, str):
        raw_confidence = raw_confidence.strip()
        if not raw_confidence:
            return None
        try:
            raw_confidence = float(raw_confidence)
        except ValueError:
            return None
    elif isinstance(raw_confidence, (int, float)):
        raw_confidence = float(raw_confidence)
    else:
        return None
    if not math.isfinite(raw_confidence):
        return None

    # 4.组装归一化事实 + 附带合法的门控标签
    normalized: dict[str, Any] = {
        "content": content,
        "category": category,
        "confidence": raw_confidence,
    }
    for field in _FACT_CLASSIFICATION_FIELDS:
        label = _normalize_gate_label(fact.get(field))
        if label is not None:
            normalized[field] = label
    return normalized


def _parse_memory_update_response(response_content: Any) -> dict[str, Any]:
    """解析 LLM 响应为更新数据结构（剥离代码围栏，失败抛 JSONDecodeError）。

    参数：
        response_content: 模型响应 content

    返回：
        解析出的更新 dict
    """
    # 1.取纯文本，空响应直接报错
    text = _extract_text(response_content).strip()
    if not text:
        raise json.JSONDecodeError("空响应", "", 0)
    # 2.有 ```json 围栏就取围栏内内容
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    # 3.截取第一个 { 到最后一个 }（容忍模型前后附加说明文字）
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("响应中未找到 JSON 对象", text, 0)
    return json.loads(text[start : end + 1])


def _fact_content_key(content: str) -> str:
    """事实内容归一化键（去空白小写），用于去重。"""
    return " ".join(content.strip().lower().split())


def _trim_facts_to_max(facts: list[dict[str, Any]], max_facts: int) -> list[dict[str, Any]]:
    """超限时按置信度降序保留 max_facts 条（防御 null/非数值置信度）。

    参数：
        facts: 事实列表
        max_facts: 上限

    返回：
        裁剪后的事实（未超限原样返回）
    """
    if len(facts) <= max_facts:
        return facts

    # 置信度安全取值（异常/越界归 0.5）
    def confidence(fact: dict[str, Any]) -> float:
        raw = fact.get("confidence")
        if raw is None or isinstance(raw, bool):
            return 0.5
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(val, 1.0)) if math.isfinite(val) else 0.5

    # 保留置信度最高的 max_facts 条
    return sorted(facts, key=confidence, reverse=True)[:max_facts]


def _message_identity(msg: Any) -> tuple[str, str] | None:
    """消息身份：类型 + 内容前 80 字符（水位线定位用；内容变化即视为新消息）。

    参数：
        msg: 消息对象

    返回：
        (type, 文本前缀) 元组；无 type 返回 None
    """
    msg_type = getattr(msg, "type", None)
    if not msg_type:
        return None
    text = extract_message_text(msg).strip()[:80]
    return (str(msg_type), text)



class MemoryUpdater:
    """长期记忆更新器（水位线 + LLM 提取 + 门控应用 + fact CRUD）。"""

    def __init__(self, config: PrismMemConfig, storage: MemoryStorage):
        """注入配置与存储即可；LLM 懒加载。

        参数：
            config: 后端私有配置
            storage: 记忆文档存储
        """
        self._config = config
        self._storage = storage
        # LLM 懒加载句柄 + 首次失败日志标记
        self._llm: Any = None
        self._llm_error_logged = False
        # 水位线：(thread_id, user_id) -> 上次成功提取的最后一条消息身份（LRU）
        self._watermarks: OrderedDict[tuple[str | None, str | None], tuple[str, str] | None] = OrderedDict()

    # ── 模型 ────────────────────────────────────────────────────────────
    def _load_llm(self) -> Any:
        """懒加载记忆提取模型（config.model=None 即复用主模型）；失败返回 None。"""
        # 1.已加载直接复用
        if self._llm is not None:
            return self._llm
        # 2.创建失败只记一次错误并停用记忆（不阻断主链路）
        try:
            from harness.models.factory import create_chat_model

            self._llm = create_chat_model(name=self._config.model, thinking_enabled=False)
        except Exception as exc:  # noqa: BLE001 —— 模型不可用不阻断主链路
            if not self._llm_error_logged:
                logger.error(
                    "记忆提取模型创建失败，记忆更新停用（请在 config 检查模型配置）: %s",
                    exc,
                )
                self._llm_error_logged = True
            return None
        return self._llm

    # ── 文档访问 ────────────────────────────────────────────────────────
    def get_memory_data(self, user_id: str | None) -> dict[str, Any]:
        """读取用户记忆文档（不存在返回空文档）。"""
        # 未指定用户归 default 桶
        return self._storage.load(user_id or "default")

    def reload_memory_data(self, user_id: str | None) -> dict[str, Any]:
        """强制重读记忆文档。"""
        return self._storage.reload(user_id or "default")

    def clear_memory_data(self, user_id: str | None) -> dict[str, Any]:
        """清空用户记忆文档。"""
        return self._storage.clear(user_id=user_id or "default")

    def import_memory_data(
        self,
        memory_data: dict[str, Any],
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """导入记忆文档（合并：新 facts 去重追加，非空分区覆盖）。

        参数：
            memory_data: 待导入文档
            user_id: 目标用户

        返回：
            合并后的最终文档
        """
        # 1.以当前文档为基底做合并
        current = self.get_memory_data(user_id)
        incoming = memory_data or {}
        # 2.已有事实内容键集合（去重用）
        known_keys = {
            _fact_content_key(f.get("content", ""))
            for f in current.get("facts", [])
            if isinstance(f, dict)
        }
        # 3.逐条导入合法且未重复的事实
        for fact in incoming.get("facts", []) or []:
            if not isinstance(fact, dict):
                continue
            normalized = _normalize_fact(fact)
            if normalized is None:
                continue
            key = _fact_content_key(normalized["content"])
            if key in known_keys:
                continue
            known_keys.add(key)
            current["facts"].append(
                {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": normalized["content"],
                    "category": normalized["category"],
                    "confidence": normalized["confidence"],
                    "createdAt": utc_now_iso_z(),
                    "source": "import",
                }
            )
        # 4.user/history 分区：非空 summary 覆盖（刷新时间）
        for section in ("user", "history"):
            incoming_section = incoming.get(section) or {}
            if not isinstance(incoming_section, dict):
                continue
            for key, value in incoming_section.items():
                if isinstance(value, dict) and value.get("summary"):
                    current.setdefault(section, {})[key] = {
                        "summary": str(value["summary"]),
                        "updatedAt": utc_now_iso_z(),
                    }
        # 5.裁到上限并按乐观锁写回
        current["facts"] = _trim_facts_to_max(current["facts"], self._config.max_facts)
        current_rev = int(current.get("revision") or 0)
        self._storage.save(current, user_id=user_id or "default", expected_revision=current_rev)
        return current

    # ── fact CRUD ───────────────────────────────────────────────────────
    def create_fact(
        self,
        content: str,
        category: str = "context",
        confidence: float = 1.0,
        user_id: str | None = None,
        source: str = "manual",
        key: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """手动新增一条事实（工具调用）。已被 max_facts 裁掉时返回 fact_id=None。

        参数：
            content / category / confidence: 事实内容
            user_id: 目标用户
            source: 来源标记（manual/tool/…）
            key: 可选稳定标识（沿用旧 save_memory 的 key 语义）；同 key 覆盖更新，否则新建

        返回：
            (最终文档, 新建或更新后的 fact id)
        """
        document = self.get_memory_data(user_id)
        now = utc_now_iso_z()
        # 1.带 key：命中同 key 则原地覆盖更新（保留原 id），并直接写回返回
        if key:
            stripped_key = str(key).strip()
            for fact in document["facts"]:
                if str(fact.get("key", "")).strip() == stripped_key:
                    fact["content"] = str(content).strip()
                    if category:
                        fact["category"] = category
                    fact["confidence"] = float(confidence)
                    fact["source"] = source
                    fact["updatedAt"] = now
                    self._storage.save(
                        document,
                        user_id=user_id or "default",
                        expected_revision=int(document.get("revision") or 0),
                    )
                    return document, fact["id"]
        # 2.新建事实
        fact: dict[str, Any] = {
            "id": f"fact_{uuid.uuid4().hex[:8]}",
            "content": str(content).strip(),
            "category": category or "context",
            "confidence": float(confidence),
            "createdAt": now,
            "source": source,
        }
        if key:
            fact["key"] = str(key).strip()
        document["facts"].append(fact)
        # 3.裁剪后判断自己是否还在（被裁掉则返回 None）
        document["facts"] = _trim_facts_to_max(document["facts"], self._config.max_facts)
        saved_id = fact["id"] if any(f.get("id") == fact["id"] for f in document["facts"]) else None
        self._storage.save(
            document,
            user_id=user_id or "default",
            expected_revision=int(document.get("revision") or 0),
        )
        return document, saved_id

    def delete_fact(self, fact_id: str, user_id: str | None = None) -> dict[str, Any]:
        """按 id 删除一条事实。"""
        document = self.get_memory_data(user_id)
        # 过滤掉目标 id 后写回
        document["facts"] = [f for f in document["facts"] if f.get("id") != fact_id]
        self._storage.save(
            document,
            user_id=user_id or "default",
            expected_revision=int(document.get("revision") or 0),
        )
        return document

    def update_fact(
        self,
        fact_id: str,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """按 id 更新事实（缺省字段保持原值）。"""
        document = self.get_memory_data(user_id)
        # 命中目标 id 就只改传入的非 None 字段，改完即停
        for fact in document["facts"]:
            if fact.get("id") == fact_id:
                if content is not None:
                    fact["content"] = str(content).strip()
                if category is not None:
                    fact["category"] = category
                if confidence is not None:
                    fact["confidence"] = float(confidence)
                break
        self._storage.save(
            document,
            user_id=user_id or "default",
            expected_revision=int(document.get("revision") or 0),
        )
        return document

    # ── 水位线 ──────────────────────────────────────────────────────────
    def _watermark_key(self, thread_id: str | None, user_id: str | None) -> tuple[str | None, str | None]:
        """水位线键：(thread_id, user_id)。"""
        return (thread_id, user_id)

    def _watermark_get(self, key: tuple[str | None, str | None]) -> tuple[str, str] | None:
        """读水位线并标记 LRU 最近使用。"""
        if key not in self._watermarks:
            return None
        self._watermarks.move_to_end(key)
        return self._watermarks[key]

    def _watermark_set(self, key: tuple[str | None, str | None], identity: tuple[str, str] | None) -> None:
        """写水位线（LRU 超限逐出最旧；cap=0 表示不限）。"""
        self._watermarks[key] = identity
        self._watermarks.move_to_end(key)
        cap = self._config.watermark_max_keys
        if cap > 0 and len(self._watermarks) > cap:
            self._watermarks.popitem(last=False)

    def _feed_after_watermark(
        self,
        key: tuple[str | None, str | None],
        messages: list[Any],
    ) -> list[Any]:
        """返回水位线之后的新消息；找不到水位线消息则喂全量（宁可多提）。

        参数：
            key: 水位线键
            messages: 全量消息

        返回：
            需提取的新消息
        """
        last_id = self._watermark_get(key)
        # 1.无水位线（首次）→ 全量
        if last_id is None:
            return messages
        # 2.定位上次末条，取其后的新消息
        for i, msg in enumerate(messages):
            if _message_identity(msg) == last_id:
                return messages[i + 1 :]
        # 3.定位不到（历史被压缩等）→ 退回全量，避免漏提
        return messages

    # ── 提示词组装 ──────────────────────────────────────────────────────
    def _build_signal_hints(self, signals: frozenset[str]) -> str:
        """把命中的信号类翻译成提取提示词 hint（事实强化入口）。

        参数：
            signals: detect_signals 命中的信号名集合

        返回：
            拼进提示词的 hint 文本（可为空串）
        """
        hints: list[str] = []
        # 每命中一类信号，追加一段「只有 durable/user-level 才记」的约束提示
        if "correction" in signals:
            hints.append(
                "IMPORTANT: Explicit correction signals detected. Record a correction with "
                "confidence >= 0.95 only when it is a durable, user-level working preference; "
                "a correction to the current task is thread/project-scoped and must not be stored."
            )
        if "reinforcement" in signals:
            hints.append(
                "IMPORTANT: Positive reinforcement signals detected. Record the confirmed "
                "approach/style with high confidence only if it is a durable, user-level pattern; "
                "approval of the current result is thread-scoped."
            )
        if "preference" in signals:
            hints.append(
                "IMPORTANT: A preference signal was detected. Record it with high confidence only "
                "when it is a durable, user-level preference; a one-off choice is thread-scoped."
            )
        if "identity" in signals:
            hints.append(
                "IMPORTANT: An identity signal was detected. Record the user's stated role or "
                "background only when it is user-level and durable across tasks."
            )
        if "goal" in signals:
            hints.append(
                "IMPORTANT: A goal signal was detected. Record only a durable, user-level goal; "
                "the objective of the current task must not be stored."
            )
        if "decision" in signals:
            hints.append(
                "IMPORTANT: A decision signal was detected. Record only a durable, user-level "
                "decision; a choice made for the current task is thread-scoped."
            )
        return "\n".join(hints)

    def _prepare_update_prompt(
        self,
        messages: list[Any],
        user_id: str | None,
        signals: frozenset[str],
    ) -> tuple[dict[str, Any], list[Any]] | None:
        """加载当前记忆并组装提取提示词；无可提取内容返回 None。

        参数：
            messages: 待提取消息
            user_id: 目标用户
            signals: 命中信号集合

        返回：
            (当前记忆, 渲染后的 prompt 消息列表) 或 None
        """
        # 1.无消息直接跳过
        if not messages:
            return None
        current_memory = self.get_memory_data(user_id)
        # 2.对话文本为空也跳过
        conversation_text = format_conversation_for_update(messages)
        if not conversation_text.strip():
            return None
        # 3.组装模板变量（当前记忆 JSON + 对话文本 + 信号 hint）
        correction_hint = self._build_signal_hints(signals)
        variables = {
            "current_memory": json.dumps(
                current_memory,
                ensure_ascii=False,
                indent=2,
            ),
            "conversation": conversation_text,
            "correction_hint": correction_hint,
        }
        # 4.渲染 chat 模板
        prompt = load_prompt_messages(
            "memory_update",
            variables,
            prompts_dir=self._config.prompts_dir,
        )
        return current_memory, prompt

    # ── 更新主流程 ──────────────────────────────────────────────────────
    def update_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        user_id: str | None = None,
        signals: frozenset[str] = frozenset(),
        *,
        bypass_watermark: bool = False,
    ) -> bool:
        """同步更新记忆（Timer 线程 / 显式调用用）。

        任何失败都返回 False 并记日志；水位线不推进 → 下轮重喂。
        """
        # 1.若已在运行的事件循环里，跨线程调度到该循环并等结果（保持同步语义）
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            return asyncio.run_coroutine_threadsafe(
                self.aupdate_memory(
                    messages,
                    thread_id=thread_id,
                    user_id=user_id,
                    signals=signals,
                    bypass_watermark=bypass_watermark,
                ),
                loop,
            ).result()
        # 2.没有运行中的循环（Timer 线程）→ 直接同步跑
        return self._do_update_memory_sync(
            messages,
            thread_id=thread_id,
            user_id=user_id,
            signals=signals,
            bypass_watermark=bypass_watermark,
        )

    async def aupdate_memory(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        user_id: str | None = None,
        signals: frozenset[str] = frozenset(),
        *,
        bypass_watermark: bool = False,
    ) -> bool:
        """异步更新记忆：把同步 LLM 调用丢到线程池，不阻塞事件循环。"""
        return await asyncio.to_thread(
            self._do_update_memory_sync,
            messages,
            thread_id=thread_id,
            user_id=user_id,
            signals=signals,
            bypass_watermark=bypass_watermark,
        )

    def _do_update_memory_sync(
        self,
        messages: list[Any],
        thread_id: str | None = None,
        user_id: str | None = None,
        signals: frozenset[str] = frozenset(),
        *,
        bypass_watermark: bool = False,
    ) -> bool:
        """同步提取内部实现（Timer 线程直接跑 sync invoke 最稳）。

        返回：
            True=成功（含水位线后无新消息的空更新）；False=失败（下轮重喂）
        """
        user_id = user_id or "default"
        watermark_key = self._watermark_key(thread_id, user_id)

        try:
            # 1. 水位线切分：紧急冲刷（bypass）喂全量，否则只喂新增
            if bypass_watermark:
                feed_messages = messages
            else:
                feed_messages = self._feed_after_watermark(watermark_key, messages)
            # 1.1 无新消息：算成功，直接返回（不推进也不报错）
            if not feed_messages:
                logger.debug("记忆更新跳过：水位线后无新消息（thread=%s）", thread_id)
                return True

            # 2. 对实际输入重检信号 + 组装提示词
            feed_signals = detect_signals(
                feed_messages,
                patterns_dir=self._config.patterns_dir,
            )
            prepared = self._prepare_update_prompt(feed_messages, user_id, frozenset(feed_signals))
            if prepared is None:
                return False
            current_memory, prompt = prepared

            # 3. LLM 提取（同步 invoke）；模型不可用则失败
            model = self._load_llm()
            if model is None:
                return False
            response = model.invoke(prompt)

            # 4. 门控应用 + 乐观写回
            update_data = _parse_memory_update_response(response.content)
            updated = self._apply_updates(
                current_memory,
                update_data,
                thread_id=thread_id,
            )
            expected_revision = int(current_memory.get("revision") or 0)
            self._storage.save(
                updated,
                user_id=user_id,
                expected_revision=expected_revision,
            )
        # 分类型处理异常：解析/文档损坏/其它，都返回 False 不推进水位线
        except json.JSONDecodeError as exc:
            logger.warning("记忆提取响应解析失败（thread=%s）: %s", thread_id, exc)
            return False
        except MemoryStorageCorruption as exc:
            logger.error("记忆文档损坏，本次更新中止（thread=%s）: %s", thread_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001 —— 记忆更新 best-effort，绝不扩散
            logger.exception("记忆更新失败（thread=%s）: %s", thread_id, exc)
            return False

        # 5. 成功且非紧急冲刷 → 把水位线推进到最后一条消息
        if not bypass_watermark:
            self._watermark_set(watermark_key, _message_identity(messages[-1]))
        logger.info(
            "记忆更新成功（thread=%s user=%s feed=%d）",
            thread_id,
            user_id,
            len(feed_messages),
        )
        return True

    # ── 门控应用 ────────────────────────────────────────────────────────
    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """把 LLM 更新数据应用到记忆文档（确定性门控，返回新文档）。

        参数：
            current_memory: 当前文档（原地修改后返回）
            update_data: 解析后的更新 JSON
            thread_id: 来源线程（记入新事实 source）

        返回：
            应用更新后的文档
        """
        now = utc_now_iso_z()
        # 门控拒绝计数（末尾汇总日志）
        rejected: dict[str, int] = {}

        def reject(kind: str) -> None:
            rejected[kind] = rejected.get(kind, 0) + 1

        # 1. user / history 摘要分区：shouldUpdate + 非空 summary + 过 scope 门控才写
        for section_name, section_keys in (
            ("user", _USER_SECTIONS),
            ("history", _HISTORY_SECTIONS),
        ):
            updates = update_data.get(section_name, {}) or {}
            if not isinstance(updates, dict):
                continue
            for key in section_keys:
                section_data = updates.get(key)
                # 1.1 结构/开关/内容任一不满足则跳过
                if (
                    not isinstance(section_data, dict)
                    or not section_data.get("shouldUpdate")
                    or not section_data.get("summary")
                ):
                    continue
                # 1.2 门控不过记数跳过
                if _summary_scope_gate_reason(section_data) is not None:
                    reject(f"summary_{key}")
                    continue
                current_memory.setdefault(section_name, {})[key] = {
                    "summary": str(section_data["summary"]).strip(),
                    "updatedAt": now,
                }

        # 2. 新事实：归一 → scope 门控 → 置信度阈值 → 内容去重 → 追加
        existing_keys = {
            _fact_content_key(f.get("content", ""))
            for f in current_memory.get("facts", [])
            if isinstance(f, dict) and isinstance(f.get("content"), str)
        }
        # 记录每条新事实在候选列表中的内容键（供矛盾移除的 replacement 校验）
        replacement_keys: dict[int, str] = {}
        new_facts = update_data.get("newFacts", []) or []
        if not isinstance(new_facts, list):
            new_facts = []
        for index, fact in enumerate(new_facts):
            normalized = _normalize_fact(fact)
            if normalized is None:
                reject("fact_malformed")
                continue
            if _fact_scope_gate_reason(normalized) is not None:
                reject("fact_scope")
                continue
            if normalized["confidence"] < self._config.fact_confidence_threshold:
                reject("fact_confidence")
                continue
            fact_key = _fact_content_key(normalized["content"])
            replacement_keys[index] = fact_key
            # 已存在同内容事实则不重复追加
            if fact_key in existing_keys:
                continue
            existing_keys.add(fact_key)
            current_memory["facts"].append(
                {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": normalized["content"],
                    "category": normalized["category"],
                    "confidence": normalized["confidence"],
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
            )

        # 3. 事实上限裁剪
        current_memory["facts"] = _trim_facts_to_max(
            current_memory["facts"],
            self._config.max_facts,
        )

        # 4. 矛盾移除（必须带 scope=user + reason；若声明替代事实，替代事实须真实已入库）
        fact_ids_to_remove: set[str] = set()
        removals = update_data.get("factsToRemove", []) or []
        if not isinstance(removals, list):
            removals = []
        for removal in removals:
            if not isinstance(removal, dict):
                reject("removal_malformed")
                continue
            if _removal_scope_gate_reason(removal) is not None:
                reject("removal_scope")
                continue
            fact_id = removal.get("id")
            if not isinstance(fact_id, str) or not fact_id:
                reject("removal_malformed")
                continue
            # 4.1 声明了 replacementFactIndex：替代事实必须索引合法且确已入库
            if "replacementFactIndex" in removal:
                ridx = removal.get("replacementFactIndex")
                if (
                    not isinstance(ridx, int)
                    or isinstance(ridx, bool)
                    or ridx < 0
                    or replacement_keys.get(ridx) is None
                    or not any(
                        f.get("id") != fact_id
                        and _fact_content_key(f.get("content", "")) == replacement_keys[ridx]
                        for f in current_memory.get("facts", [])
                    )
                ):
                    reject("removal_replacement")
                    continue
            fact_ids_to_remove.add(fact_id)

        # 4.2 统一执行删除
        if fact_ids_to_remove:
            current_memory["facts"] = [
                f for f in current_memory["facts"] if f.get("id") not in fact_ids_to_remove
            ]

        # 5.汇总门控拒绝统计
        if rejected:
            logger.info(
                "记忆更新门控统计（thread=%s）: 拒绝 %s",
                thread_id,
                {k: v for k, v in sorted(rejected.items())},
            )
        return current_memory
