from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable

from app.core.event_bus import get_event_bus
from app.core.events import RunEventType, make_event

"""
    agent 装配注册表（agent_registry）——应用级 Lead Agent 的统一入口。

    关键设计：harness 的 event_sink（工具事件唯一出口）是「注入式」的——
    build_lead_agent 时固定进中间件，而工具事件本身不带 run 标识。为了让
    agent 能被安全缓存、同时支持并发多 run 事件归位，这里采用
    「全局分发 sink + run 上下文变量」：
      1. DispatchEventSink 是进程级单例，作为所有缓存 agent 的 event_sink；
      2. run 执行期间（run_worker 内）设置 _CURRENT_RUN contextvar，工具
         事件据此路由到对应 run 的 EventBus（参考 harness 的 user_context
         模式，机制相同）；
      3. 没有 run 上下文时（测试 / 直连场景）事件被忽略，不产生副作用。

    AgentRegistry 按配置 key 做 LRU 缓存：key 含 模型名 / 思考开关 / 技能
    目录 / 沙箱与 checkpointer 身份 / 配置摘要——任何一项变化都会自动重建。
"""

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 4


_CURRENT_RUN: ContextVar[str | None] = ContextVar("prism_current_run", default=None)
_CURRENT_THREAD: ContextVar[str | None] = ContextVar("prism_current_thread", default=None)


@contextmanager
def run_context(run_id: str, thread_id: str):
    """进入一次 run 的执行上下文（run_worker 内包裹 astream 用）。

    期间 DispatchEventSink 收到的工具事件会路由到该 run；退出时自动恢复
    上一个上下文（支持嵌套/并发，contextvar 隔离）。
    """
    run_token = _CURRENT_RUN.set(run_id)
    thread_token = _CURRENT_THREAD.set(thread_id)
    try:
        yield
    finally:
        _CURRENT_RUN.reset(run_token)
        _CURRENT_THREAD.reset(thread_token)


class DispatchEventSink:
    """进程级事件分发 sink：把 harness 工具事件路由到当前 run 的 EventBus。

    作为所有缓存 agent 的 event_sink 注入；事件归属由 _CURRENT_RUN 决定。
    """

    def __init__(self, *, bus: Any | None = None) -> None:
        """初始化；bus 缺省用进程级 EventBus 单例。"""
        self._bus = bus

    async def __call__(self, event: dict[str, Any]) -> None:
        """接收 harness 工具事件并路由发布（无 run 上下文则忽略）。"""
        run_id = _CURRENT_RUN.get()
        if not run_id:
            return
        kind = event.get("event")
        if kind == "tool_start":
            run_event = make_event(
                RunEventType.TOOL_START,
                run_id=run_id,
                thread_id=_CURRENT_THREAD.get() or "",
                payload={
                    "tool": event.get("tool", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                    "args_preview": event.get("args_preview", ""),
                    "ts": event.get("ts"),
                },
            )
        elif kind == "tool_end":
            run_event = make_event(
                RunEventType.TOOL_END,
                run_id=run_id,
                thread_id=_CURRENT_THREAD.get() or "",
                payload={
                    "tool": event.get("tool", ""),
                    "tool_call_id": event.get("tool_call_id", ""),
                    "duration_seconds": event.get("duration_seconds"),
                    "ts": event.get("ts"),
                },
            )
        else:
            return
        await (self._bus or get_event_bus()).publish(run_event)


_dispatch_sink: DispatchEventSink | None = None
_dispatch_sink_lock = threading.Lock()


def get_dispatch_sink() -> DispatchEventSink:
    """返回进程级事件分发 sink 单例（懒构造，线程安全）。"""
    global _dispatch_sink
    if _dispatch_sink is not None:
        return _dispatch_sink
    with _dispatch_sink_lock:
        if _dispatch_sink is None:
            _dispatch_sink = DispatchEventSink()
        return _dispatch_sink


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
        """初始化；builder 覆盖构建器（默认 harness build_lead_agent）。

        Args:
            sandbox_provider: 沙箱懒加载提供者。sandbox 显式传入时优先用
                显式值；否则首次 build/get 时调用 provider 取真实沙箱实例
                （进程级主动管理容器，首用懒启动 + 显式 stop 回收）。
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
        """解析装配用的沙箱实例（显式值优先，否则经 provider 懒启动一次）。"""
        if self._sandbox is not None:
            return self._sandbox
        if self._sandbox_provider is None:
            return None
        with self._lock:
            if self._sandbox is not None:
                return self._sandbox
            try:
                self._sandbox = self._sandbox_provider()
            except Exception as exc:  # noqa: BLE001 —— 启动失败降级无沙箱，不阻断装配
                logger.warning("沙箱懒启动失败，本次装配不带沙箱工具: %s", exc)
                self._sandbox = None
        return self._sandbox

    @property
    def sandbox(self) -> Any | None:
        """当前装配用的沙箱实例（可能触发懒启动）。"""
        return self._resolve_sandbox()


    def _config_hash(self) -> str:
        """配置摘要：AppConfig 内容变化 → 哈希变化 → 触发重建。"""
        try:
            return hashlib.sha256(repr(self._app_config).encode("utf-8")).hexdigest()[:16]
        except Exception:  # noqa: BLE001 —— 摘要失败时用固定占位（不强依赖）
            return "cfg-unknown"

    def _key(self, *, model_name: str | None, thinking_enabled: bool) -> str:
        """计算缓存 key（模型/思考/技能/实例身份/配置任何一项变化都换 key）。"""
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
        """按参数取 agent：命中缓存直接返回，未命中构建并缓存（LRU）。"""
        key = self._key(model_name=model_name, thinking_enabled=thinking_enabled)
        with self._lock:
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
    """返回进程级 AgentRegistry 单例（按 app_config 身份复用，线程安全）。"""
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