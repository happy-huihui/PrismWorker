"""记忆更新器：把清洗后的对话增量提取为长期记忆。

职责（对应参考实现 updater 模块，去掉 staleness / consolidation）：
    1. ``update_memory``：水位线切分新消息 → 组提取提示词 → LLM 提取
       JSON → 确定性门控应用 → memory.json 乐观写回 → 推进水位线；
       全程 best-effort：任何失败只记日志返回 False，不推进水位线，
       下轮自动重喂（增量不丢）；
    2. 事实强化门控（代码层强制执行，不依赖模型自觉）：
       scope/durability/authority 分类门控 → 置信度阈值 → 内容去重 →
       矛盾移除（factsToRemove 依赖检查）→ 事实上限裁剪；
    3. 六分区摘要（user/history）增量改写，同样过 scope 门控；
    4. fact CRUD（create/delete/update）与文档级管理 op（clear/import），
       供模型工具与管理接口使用；
    5. 异步边界 ``aupdate_memory``：事件循环内调用时用 asyncio.to_thread
       包同步 LLM 调用，阻塞不落在主循环上。

LLM 提取模型：优先 ``PrismMemConfig.model`` 指定模型，否则复用主模型
（``create_chat_model(name=None)``）；两者都不可用时更新直接失败并告警。
"""

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
from harness.memory.message_processing import (
    detect_signals,
    extract_message_text,
    format_conversation_for_update,
)
from harness.memory.prompt import load_prompt_messages
from harness.memory.storage import (
    MemoryStorage,
    MemoryStorageCorruption,
    create_empty_memory,
    utc_now_iso_z,
)

logger = logging.getLogger(__name__)

# 事实分类三个门控字段（提取元数据，不落库）
_FACT_CLASSIFICATION_FIELDS = ("scope", "durability", "authority")

# 提取输出中六个分区名（user / history）
_USER_SECTIONS = ("workContext", "personalContext", "topOfMind")
_HISTORY_SECTIONS = ("recentMonths", "earlierContext", "longTermBackground")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_text(content: Any) -> str:
    """提取 LLM 响应纯文本（兼容 str 与多模态块列表）。"""
    if isinstance(content, str):
        return content
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
    return str(content)


def _normalize_gate_label(value: Any) -> str | None:
    """归一化模型产出的门控标签（去空白小写；非字符串返回 None）。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized or None


def _fact_scope_gate_reason(fact: dict[str, Any]) -> str | None:
    """返回模型事实被确定性门控拒绝的原因（None = 通过）。"""
    if any(_normalize_gate_label(fact.get(field)) is None for field in _FACT_CLASSIFICATION_FIELDS):
        return "missing"
    if _normalize_gate_label(fact.get("scope")) != "user":
        return "scope"
    if _normalize_gate_label(fact.get("durability")) != "durable":
        return "durability"
    if _normalize_gate_label(fact.get("authority")) != "descriptive":
        return "authority"
    return None


def _summary_scope_gate_reason(section_data: dict[str, Any]) -> str | None:
    """摘要分区门控：scope=user 且 authority=descriptive 才可写。"""
    scope = _normalize_gate_label(section_data.get("scope"))
    authority = _normalize_gate_label(section_data.get("authority"))
    if scope is None or authority is None:
        return "missing"
    if scope != "user":
        return "scope"
    if authority != "descriptive":
        return "authority"
    return None


def _removal_scope_gate_reason(removal: dict[str, Any]) -> str | None:
    """矛盾移除门控：必须带 scope=user 与非空 reason。"""
    scope = _normalize_gate_label(removal.get("scope"))
    reason = removal.get("reason")
    if scope is None or not isinstance(reason, str) or not reason.strip():
        return "missing"
    if scope != "user":
        return "scope"
    return None


def _normalize_fact(fact: Any) -> dict[str, Any] | None:
    """归一化一条模型事实（校验内容/类别/置信度/门控标签）。"""
    if not isinstance(fact, dict):
        return None
    raw_content = fact.get("content")
    if not isinstance(raw_content, str):
        return None
    content = raw_content.strip()
    if not content:
        return None

    raw_category = fact.get("category")
    category = raw_category.strip() if isinstance(raw_category, str) and raw_category.strip() else "context"

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
    """解析 LLM 响应为更新数据结构（剥离代码围栏，失败抛 JSONDecodeError）。"""
    text = _extract_text(response_content).strip()
    if not text:
        raise json.JSONDecodeError("空响应", "", 0)
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    # 找到第一个 { 与最后一个 } 之间的部分（容忍模型附加说明）
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise json.JSONDecodeError("响应中未找到 JSON 对象", text, 0)
    return json.loads(text[start : end + 1])


def _fact_content_key(content: str) -> str:
    """事实内容归一化键（去空白小写），用于去重。"""
    return " ".join(content.strip().lower().split())


def _trim_facts_to_max(facts: list[dict[str, Any]], max_facts: int) -> list[dict[str, Any]]:
    """超限时按置信度升序裁剪（防御 null/非数值置信度）。"""
    if len(facts) <= max_facts:
        return facts

    def confidence(fact: dict[str, Any]) -> float:
        raw = fact.get("confidence")
        if raw is None or isinstance(raw, bool):
            return 0.5
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(val, 1.0)) if math.isfinite(val) else 0.5

    return sorted(facts, key=confidence, reverse=True)[:max_facts]


def _message_identity(msg: Any) -> tuple[str, str] | None:
    """消息身份：类型 + 内容前 80 字符（水位线定位用；内容变化即视为新消息）。"""
    msg_type = getattr(msg, "type", None)
    if not msg_type:
        return None
    text = extract_message_text(msg).strip()[:80]
    return (str(msg_type), text)


class MemoryUpdater:
    """长期记忆更新器（水位线 + LLM 提取 + 门控应用 + fact CRUD）。"""

    def __init__(self, config: PrismMemConfig, storage: MemoryStorage):
        """注入配置与存储即可；LLM 懒加载。"""
        self._config = config
        self._storage = storage
        self._llm: Any = None
        self._llm_error_logged = False
        self._watermarks: OrderedDict[tuple[str | None, str | None], tuple[str, str] | None] = OrderedDict()

    # ── 模型 ────────────────────────────────────────────────────────────
    def _load_llm(self) -> Any:
        """懒加载记忆提取模型（None = 复用主模型）；失败返回 None。"""
        if self._llm is not None:
            return self._llm
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
        """导入记忆文档（合并：新 facts 去重追加，非空分区覆盖）。"""
        current = self.get_memory_data(user_id)
        incoming = memory_data or {}
        known_keys = {
            _fact_content_key(f.get("content", ""))
            for f in current.get("facts", [])
            if isinstance(f, dict)
        }
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

        key: 可选的稳定标识（模型工具沿用旧 save_memory 的 key 语义）；
            同 key 已存在时覆盖更新，否则新建。
        """
        document = self.get_memory_data(user_id)
        now = utc_now_iso_z()
        if key:
            stripped_key = str(key).strip()
            for fact in document["facts"]:
                if str(fact.get("key", "")).strip() == stripped_key:
                    # 同 key 覆盖更新（保留原 id，刷新内容与时间）
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
        """按 id 更新事实（缺省字段保持）。"""
        document = self.get_memory_data(user_id)
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
        return (thread_id, user_id)

    def _watermark_get(self, key: tuple[str | None, str | None]) -> tuple[str, str] | None:
        """读水位线并标记 LRU 最近使用。"""
        if key not in self._watermarks:
            return None
        self._watermarks.move_to_end(key)
        return self._watermarks[key]

    def _watermark_set(self, key: tuple[str | None, str | None], identity: tuple[str, str] | None) -> None:
        """写水位线（LRU 超限逐出最旧；0 = 不限）。"""
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
        """返回水位线之后的新消息；找不到水位线消息则喂全量（宁可多提）。"""
        last_id = self._watermark_get(key)
        if last_id is None:
            return messages
        for i, msg in enumerate(messages):
            if _message_identity(msg) == last_id:
                return messages[i + 1 :]
        return messages

    # ── 提示词组装 ──────────────────────────────────────────────────────
    def _build_signal_hints(self, signals: frozenset[str]) -> str:
        """把命中的信号类翻译成提取提示词 hint（事实强化入口）。"""
        hints: list[str] = []
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
        """加载当前记忆并组装提取提示词；无可提取内容返回 None。"""
        if not messages:
            return None
        current_memory = self.get_memory_data(user_id)
        conversation_text = format_conversation_for_update(messages)
        if not conversation_text.strip():
            return None
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
        """同步提取内部实现（Timer 线程直接跑 sync invoke 最稳）。"""
        user_id = user_id or "default"
        watermark_key = self._watermark_key(thread_id, user_id)

        try:
            # 1. 水位线切分
            if bypass_watermark:
                feed_messages = messages
            else:
                feed_messages = self._feed_after_watermark(watermark_key, messages)
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

            # 3. LLM 提取（同步 invoke）
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
        except json.JSONDecodeError as exc:
            logger.warning("记忆提取响应解析失败（thread=%s）: %s", thread_id, exc)
            return False
        except MemoryStorageCorruption as exc:
            logger.error("记忆文档损坏，本次更新中止（thread=%s）: %s", thread_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001 —— 记忆更新 best-effort，绝不扩散
            logger.exception("记忆更新失败（thread=%s）: %s", thread_id, exc)
            return False

        # 5. 成功且非紧急冲刷 → 推进水位线
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
        """把 LLM 更新数据应用到记忆文档（确定性门控，返回新文档）。"""
        now = utc_now_iso_z()
        rejected: dict[str, int] = {}

        def reject(kind: str) -> None:
            rejected[kind] = rejected.get(kind, 0) + 1

        # 1. user / history 摘要分区
        for section_name, section_keys in (
            ("user", _USER_SECTIONS),
            ("history", _HISTORY_SECTIONS),
        ):
            updates = update_data.get(section_name, {}) or {}
            if not isinstance(updates, dict):
                continue
            for key in section_keys:
                section_data = updates.get(key)
                if (
                    not isinstance(section_data, dict)
                    or not section_data.get("shouldUpdate")
                    or not section_data.get("summary")
                ):
                    continue
                if _summary_scope_gate_reason(section_data) is not None:
                    reject(f"summary_{key}")
                    continue
                current_memory.setdefault(section_name, {})[key] = {
                    "summary": str(section_data["summary"]).strip(),
                    "updatedAt": now,
                }

        # 2. 新事实（scope 门控 → 置信度 → 去重）
        existing_keys = {
            _fact_content_key(f.get("content", ""))
            for f in current_memory.get("facts", [])
            if isinstance(f, dict) and isinstance(f.get("content"), str)
        }
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

        # 4. 矛盾移除（必须带 scope=user + reason；依赖替代事实必须真实存在）
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

        if fact_ids_to_remove:
            current_memory["facts"] = [
                f for f in current_memory["facts"] if f.get("id") not in fact_ids_to_remove
            ]

        if rejected:
            logger.info(
                "记忆更新门控统计（thread=%s）: 拒绝 %s",
                thread_id,
                {k: v for k, v in sorted(rejected.items())},
            )
        return current_memory