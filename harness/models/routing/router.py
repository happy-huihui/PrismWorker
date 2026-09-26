"""模型动态路由（router）。

职责：给定「这一轮的输入 + 可选配置」，决定该用哪个模型，并说明原因。
       是「挑选模型」的唯一决策点，取代了原先由前端下拉框人工选模型的做法。

为什么放在 harness 而不是 app 层：
    路由结果直接喂给 harness 的模型工厂（create_chat_model），
    且需要读 harness 的 AppConfig；放同一层避免 app → harness 反向依赖。

与 factory 的分工：
    - router  决定「用哪个 name」（策略层，可解释、可单测）
    - factory 决定「这个 name 怎么造成实例」（构造层，带缓存）

对外暴露：
    - ModelRouter        路由决策器
    - get_model_router   进程级单例
    - reset_model_router 重置单例（测试隔离）

用法示例：
    router = get_model_router(app_config)
    decision = router.decide(messages, thinking_enabled=False)
    decision.model_name   # "deepseek-flash"
    decision.reason       # "使用默认模型 deepseek-flash"
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from harness.config.app_config import AppConfig, get_app_config
from harness.config.routing_config import RoutingConfig
from harness.models.routing.decision import RoutingDecision, RoutingSignals
from harness.models.routing.rules import RULES, build_signals, rule_default

logger = logging.getLogger(__name__)


class ModelRouter:
    """按规则从配置里挑一个模型，并给出可展示的决策原因。

    无状态（除了持有的配置引用），可安全并发调用。
    """

    def __init__(self, app_config: AppConfig | None = None) -> None:
        """初始化。

        参数：
            app_config: 应用配置；None 用全局单例
        """
        self._app_config = app_config

    @property
    def config(self) -> AppConfig:
        """当前生效的应用配置（延迟取全局单例）。"""
        return self._app_config or get_app_config()

    @property
    def routing(self) -> RoutingConfig:
        """路由子配置。"""
        return self.config.routing

    def decide(
        self,
        messages: Any,
        *,
        thinking_enabled: bool = False,
        explicit_model: str | None = None,
    ) -> RoutingDecision:
        """对一轮输入做路由决策。

        参数：
            messages: 本轮输入消息（dict 或 BaseMessage 序列）
            thinking_enabled: 调用方是否显式开了深度思考
            explicit_model: 调用方显式指定的模型名（指定即不路由）

        返回：
            RoutingDecision（model_name 为 None 表示交给工厂走激活模型）

        降级：路由关闭（routing.enabled=False）时直接用调用方给的名字
            （或 None），并标注 source="fallback"，行为与改造前完全一致。
        """
        config = self.routing

        # 路由关闭：保持旧行为，不猜、不升档
        if not config.enabled:
            return RoutingDecision(
                model_name=explicit_model,
                source="fallback",
                reason="模型路由已关闭，使用默认配置",
                requested=explicit_model,
            )

        signals: RoutingSignals = build_signals(
            messages,
            explicit_model=explicit_model,
            thinking_enabled=thinking_enabled,
        )

        # 逐条试规则，先命中先返回
        for rule in RULES:
            decision = rule(signals, config)
            if decision is not None:
                return self._validate(decision)

        # 规则表全部未命中 → 走兜底默认档（同样要校验配置里的模型名）
        return self._validate(rule_default(signals, config))

    def _validate(self, decision: RoutingDecision) -> RoutingDecision:
        """校验决策指向的模型名是否真实存在，不存在则退回激活模型。

        防的是「config.yaml 里 routing.default_model / escalate_model 拼错」
        这类配置错误——直接报错会打断整轮会话，退回默认档体验更好。

        参数：
            decision: 待校验的决策

        返回：
            原决策（校验通过）或退回激活模型的 fallback 决策
        """
        if not decision.model_name:
            return decision
        if self._model_exists(decision.model_name):
            return decision
        logger.warning(
            "路由指向的模型 %s 不在 config.models 中，退回激活模型",
            decision.model_name,
        )
        return RoutingDecision(
            model_name=None,
            source="fallback",
            reason=f"路由目标模型 {decision.model_name} 未配置，使用系统激活模型",
        )

    def _model_exists(self, name: str) -> bool:
        """该模型名是否在 config.models 里登记。"""
        return self.config.get_model_config(name) is not None

    def describe(self) -> dict[str, Any]:
        """输出当前路由配置的可读摘要（自检 / 接口暴露用）。"""
        config = self.routing
        return {
            "enabled": config.enabled,
            "default_model": config.default_model,
            "escalate_model": config.escalate_model,
            "vision_model": config.vision_model,
            "keyword_count": len(config.escalate_keywords),
        }


_router: ModelRouter | None = None
_router_lock = threading.Lock()


def get_model_router(app_config: AppConfig | None = None) -> ModelRouter:
    """返回进程级 ModelRouter 单例（线程安全）。

    参数：
        app_config: 首次构造时绑定的配置；后续调用忽略该参数
            （配置热重载走 reset_model_router）
    """
    global _router
    if _router is not None:
        return _router
    with _router_lock:
        if _router is None:
            _router = ModelRouter(app_config)
        return _router


def reset_model_router() -> None:
    """清空单例（测试隔离 / 配置热重载）。"""
    global _router
    with _router_lock:
        _router = None
