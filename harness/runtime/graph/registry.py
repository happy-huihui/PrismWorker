from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Callable

from harness.runtime.events import get_dispatch_sink

"""Lead Agent 装配注册表（graph.registry）

    职责：按配置构建并缓存编译好的 Lead Agent 图，供 run 编排取用。
    背景：harness 的 event_sink（工具事件唯一出口）是注入式的、装配期固定，
         而工具事件本身不带 run 标识；为让 agent 能被安全缓存、又支持并发
         多 run 事件各归各位，这里把「进程级分发 sink」作为所有缓存 agent 的
         event_sink，事件归属由 run 上下文变量决定（见 events.routing）。
    缓存：按配置 key 做 LRU，key 含 模型名 / 思考开关 / 技能目录 / 沙箱与
         checkpointer 身份 / 配置摘要——任一项变化都会自动重建。

    对外暴露：
        - AgentRegistry        装配注册表（build_agent / get_agent / sandbox）
        - get_agent_registry   进程级单例（按 app_config 身份复用）
        - reset_agent_registry 重置单例（测试隔离）
"""

logger = logging.getLogger(__name__)

# 默认 LRU 容量
_DEFAULT_MAX_SIZE = 4

# 图构建器签名（缺省委托 harness build_lead_agent）
AgentBuilder = Callable[..., Any]


class AgentRegistry:
    """Lead Agent 装配注册表：按配置 key LRU 缓存编译后的图。"""

    def __init__(
        self,
        *,
        app_config: Any,
        sandbox: Any | None = None,
        sandbox_provider: Callable[[], Any] | None = None,
        checkpointer: Any | None = None,
        skills_dir: str | None = None,
        builder: AgentBuilder | None = None,
        max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        """初始化。

        参数：
            app_config: harness AppConfig（模型/沙箱/工具等），也是缓存身份
            sandbox: 显式沙箱实例；传入则优先于 provider
            sandbox_provider: 沙箱懒加载提供者；无显式 sandbox 时首用调一次
            checkpointer: 缓存路径共享的 checkpointer（get_agent 用）
            skills_dir: 技能目录
            builder: 覆盖图构建器（默认 harness build_lead_agent）
            max_size: LRU 容量
        """
        self._app_config = app_config
        self._sandbox = sandbox
        self._sandbox_provider = sandbox_provider
        self._checkpointer = checkpointer
        self._skills_dir = skills_dir
        self._builder = builder
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._max_size = max(1, max_size)
        self._lock = threading.Lock()


    def _resolve_sandbox(self) -> Any | None:
        """解析装配用的沙箱实例。

        显式注入的实例（构造参数 sandbox）直接用；否则**每次装配都问一次
        provider**，绝不把它缓存下来。

        为什么不缓存（2026-09-25 实测缺陷，见 .prism-worker/logs/app.log）：
            provider（runtime.sandbox.get_app_sandbox）走的是「活跃缓存 → 热池
            提升 → 新建」状态机，其中「热池提升」会把条目从热池**摘出来**放进
            活跃集。一旦把结果缓存下来，第二次装配就完全绕过了状态机：
            同一个实例既留在 run 手里、又留在热池里被当成闲置件，
            闲置守护线程按 parked_at 计满 idle_timeout 就把它销毁（sbx.close()），
            而 run 还在用它 → 后续所有沙箱工具报「沙箱已关闭」。
            实测时间线：19:14:43 沙箱回源热池 → 19:18:51 新 run 直接拿到缓存实例
            （无「从热池提升复用」日志）→ 19:25:26 被闲置回收 → run 中途失效。
        """
        # 显式注入优先：这类实例的生命周期由调用方自己管
        if self._sandbox is not None:
            return self._sandbox
        # 无 provider 则无沙箱
        if self._sandbox_provider is None:
            return None
        # 每次都问 provider，让状态机（活跃/热池）如实流转
        try:
            return self._sandbox_provider()
        except Exception as exc:  # noqa: BLE001 —— 解析失败降级无沙箱，不阻断装配
            logger.warning("沙箱解析失败，本次装配不带沙箱工具: %s", exc)
            return None

    @property
    def sandbox(self) -> Any | None:
        """当前装配用的沙箱实例（每次调用都向 provider 取一次）。"""
        return self._resolve_sandbox()


    def _config_hash(self) -> str:
        """配置摘要：AppConfig 内容变化 → 哈希变化 → 触发重建。"""
        try:
            return hashlib.sha256(repr(self._app_config).encode("utf-8")).hexdigest()[:16]
        except Exception:  # noqa: BLE001 —— 摘要失败时用固定占位（不强依赖）
            return "cfg-unknown"

    def _key(self, *, model_name: str | None, thinking_enabled: bool) -> str:
        """计算缓存 key（模型/思考/技能/实例身份/配置任何一项变化都换 key）。

        参数：
            model_name: 模型名
            thinking_enabled: 是否思考模式

        返回：
            用于 LRU 命中的字符串键
        """
        resolved_model = model_name or "auto"
        actual = self._resolve_sandbox()
        parts = [
            f"model={resolved_model}",
            f"think={int(bool(thinking_enabled))}",
            f"skills={self._skills_dir or 'default'}",
            f"sandbox={id(actual)}",
            f"ckpt={id(self._checkpointer)}",
            f"cfg={self._config_hash()}",
        ]
        return "|".join(parts)


    def build_agent(
        self,
        *,
        model_name: str | None = None,
        thinking_enabled: bool = False,
        checkpointer: Any | None = None,
    ) -> Any:
        """按需构建一个不查、不写缓存的全新 agent（本次 run 专属 checkpointer 用）。

        参数：
            model_name: 模型名
            thinking_enabled: 是否思考模式
            checkpointer: 本次编译绑定的 checkpointer

        返回：
            新编译的 agent 图

        与 get_agent 的区别：图在编译时绑定 checkpointer 实例，而按 run 新建
        的 checkpointer 每次都是新实例 → 缓存无法复用，只能现场装配。仍走
        同一构建路径（builder/沙箱/技能/事件分发），保证装配一致性。
        """
        builder = self._builder or _default_builder
        return builder(
            self._app_config,
            sandbox=self._resolve_sandbox(),
            checkpointer=checkpointer,
            model_name=model_name,
            thinking_enabled=thinking_enabled,
            event_sink=get_dispatch_sink(),
            skills_dir=self._skills_dir,
        )

    def get_agent(
        self,
        *,
        model_name: str | None = None,
        thinking_enabled: bool = False,
    ) -> Any:
        """按参数取 agent：命中缓存直接返回，未命中构建并缓存（LRU）。

        参数：
            model_name: 模型名
            thinking_enabled: 是否思考模式

        返回：
            缓存或新建的 agent 图
        """
        key = self._key(model_name=model_name, thinking_enabled=thinking_enabled)
        with self._lock:
            # 命中则移到队尾（最近使用）后返回
            cached = self._cache.pop(key, None)
            if cached is not None:
                self._cache[key] = cached
                return cached
            agent = self.build_agent(
                model_name=model_name,
                thinking_enabled=thinking_enabled,
                checkpointer=self._checkpointer,
            )
            self._cache[key] = agent
            # 超容量则从队首淘汰最久未用
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            return agent

    def reset_all(self) -> None:
        """清空全部缓存（测试隔离 / 配置整体重建用）。"""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        """当前缓存条目数。"""
        with self._lock:
            return len(self._cache)


def _default_builder(*args: Any, **kwargs: Any) -> Any:
    """默认构建器：委托 harness 官方入口 build_lead_agent。"""
    from harness.agents.lead_agent.agent import build_lead_agent

    return build_lead_agent(*args, **kwargs)



_registries: dict[int, AgentRegistry] = {}
_registry_lock = threading.Lock()


def get_agent_registry(
    *,
    app_config: Any,
    sandbox: Any | None = None,
    sandbox_provider: Callable[[], Any] | None = None,
    checkpointer: Any | None = None,
    skills_dir: str | None = None,
    builder: AgentBuilder | None = None,
    max_size: int = _DEFAULT_MAX_SIZE,
) -> AgentRegistry:
    """返回进程级 AgentRegistry 单例（按 app_config 身份复用，线程安全）。

    参数：
        app_config: 用作单例键的配置实例（id 相同即复用）
        其余参数见 AgentRegistry 构造

    返回：
        对应 app_config 的 AgentRegistry 单例
    """
    key = id(app_config)
    with _registry_lock:
        registry = _registries.get(key)
        if registry is not None:
            return registry
        registry = AgentRegistry(
            app_config=app_config,
            sandbox=sandbox,
            sandbox_provider=sandbox_provider,
            checkpointer=checkpointer,
            skills_dir=skills_dir,
            builder=builder,
            max_size=max_size,
        )
        _registries[key] = registry
        return registry


def reset_agent_registry() -> None:
    """清空注册表单例（测试隔离用）。"""
    global _registries
    with _registry_lock:
        _registries = {}
