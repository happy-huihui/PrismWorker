
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Any

from langchain_core.language_models import BaseChatModel

from harness.config.app_config import AppConfig, get_app_config
from harness.config.model_config import ModelConfig
from harness.models.capabilities import (
    find_model,
    supports_thinking,
    supports_vision,
)
from harness.models.deepseek_models import (
    DeepSeekFlashChatModel,
    DeepSeekProChatModel,
    PatchedChatDeepSeek,
    is_deepseek_pro_model,
    restore_reasoning_content,
)
from harness.models.mimo_models import (
    MiMoFlashChatModel,
    MiMoProChatModel,
    is_mimo_flash_model,
    is_mimo_pro_model,
)
from harness.models.registry import get_strategy, supported_providers

"""模型工厂

    职责：根据 ModelConfig ，实例化 Model
    流程：解析配置、合并参数、按 provider 选择策略 -> 委托实例化，具体的实例化细节由各 provider 策略承担。

    缓存：模型实例按 (配置, 模型名, 思考开关) LRU 复用。这是首帧提速的关键——
        实测构造一个 ChatDeepSeek 要 2.7~9.6 秒（内部两个 httpx 客户端各约 0.9 秒，
        Windows 上加载 SSL 信任库很慢），而 run 装配每次都重新造，等于每条会话
        白等好几秒。带覆盖参数的调用（采样温度等不同参数）不进缓存，避免串参数。

    降级：请求了思考但模型 supports_thinking=false 时不再抛错，忽略该开关继续跑
        （是否真的被降级由 runtime 层查 supports_thinking 告知前端）。

    对外暴露：
        - create_chat_model()   工厂入口，按 provider 分流创建模型
        - reset_model_cache()   清空模型实例缓存（测试 / 配置热重载）
        - supports_vision()     查询模型是否支持视觉（委托 capabilities）
        - supports_thinking()   查询模型是否支持思考（委托 capabilities）
"""

logger = logging.getLogger(__name__)

# 模型实例缓存（LRU）；容量按「模型数 × 思考开关」留余量
_MODEL_CACHE: OrderedDict[tuple[Any, ...], BaseChatModel] = OrderedDict()
_MODEL_CACHE_MAX = 16
_MODEL_CACHE_LOCK = threading.Lock()
# 已告过警的降级键，避免每条会话重复刷日志
_DEGRADE_WARNED: set[tuple[Any, ...]] = set()

# 对外 + 向后兼容符号：上游可从 harness.models.factory 直接导入这些名字，
# 其中能力探测来自 capabilities、模型实现类来自 deepseek_models，此处统一 re-export。
__all__ = [
    "create_chat_model",
    "reset_model_cache",
    "supports_vision",
    "supports_thinking",
    "find_model",
    "PatchedChatDeepSeek",
    "DeepSeekFlashChatModel",
    "DeepSeekProChatModel",
    "is_deepseek_pro_model",
    "restore_reasoning_content",
    "MiMoProChatModel",
    "MiMoFlashChatModel",
    "is_mimo_pro_model",
    "is_mimo_flash_model",
]


def reset_model_cache() -> None:
    """清空模型实例缓存（测试隔离 / 配置热重载后强制重建）。"""
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE.clear()
    _DEGRADE_WARNED.clear()


def create_chat_model(
    name: str | None = None,
    thinking_enabled: bool = False,
    *,
    app_config: AppConfig | None = None,
    model_overrides: dict | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """按配置创建聊天模型实例（工厂入口，命中缓存则复用同一实例）。

    参数：
        name: 模型在配置中的名称；None 用当前激活模型
        thinking_enabled: 是否开启思考模式（模型不支持时自动忽略，不报错）
        app_config: 显式应用配置；缺省用全局单例
        model_overrides: 调用方采样覆盖（temperature / max_tokens 等），
            值为 None 的键忽略，避免覆盖掉配置值
        kwargs: 透传给模型构造器的额外参数

    返回：
        一个 BaseChatModel 实例（ChatOpenAI 或 DeepSeek 实现类）
    """
    # 取配置：显式传入优先，否则用全局单例
    config = app_config or get_app_config()

    # 未指定名字用激活模型，指定了则按名查找（找不到直接报错）
    if name is None:
        model_config = config.active_model_config()
    else:
        model_config = config.get_model_config(name)
        if model_config is None:
            raise ValueError(f"模型 {name} 在配置中不存在") from None

    # 思考模式降级：不支持就是不开，不再抛错打断整条 run
    effective_thinking = bool(thinking_enabled) and bool(model_config.supports_thinking)
    if thinking_enabled and not effective_thinking:
        warn_key = (id(config), model_config.name)
        if warn_key not in _DEGRADE_WARNED:
            _DEGRADE_WARNED.add(warn_key)
            logger.warning(
                "模型 %s 不支持思考模式（supports_thinking=false），已忽略该开关",
                model_config.name,
            )

    # 仅「纯配置构造」可复用：带覆盖参数或额外 kwargs 的调用必须现造，
    # 否则会把 A 调用方的参数串给 B
    cacheable = not model_overrides and not kwargs
    cache_key: tuple[Any, ...] = (id(config), model_config.name, effective_thinking)
    if cacheable:
        with _MODEL_CACHE_LOCK:
            cached = _MODEL_CACHE.get(cache_key)
            if cached is not None:
                # 命中即提到 LRU 队尾（最近使用）
                _MODEL_CACHE.move_to_end(cache_key)
                return cached

    # 把配置转换为构造器参数字典
    settings = _model_to_settings(model_config)

    # 合并调用方覆盖参数，忽略值为 None 的键以免覆盖配置
    if model_overrides:
        settings.update(
            {k: v for k, v in model_overrides.items() if v is not None}
        )

    # 按 provider 选择策略并实例化模型
    model = _build_model(model_config, settings, effective_thinking, **kwargs)

    if cacheable:
        with _MODEL_CACHE_LOCK:
            _MODEL_CACHE[cache_key] = model
            # 超容量则从队首淘汰最久未用
            while len(_MODEL_CACHE) > _MODEL_CACHE_MAX:
                _MODEL_CACHE.popitem(last=False)

    # 记录创建结果，便于排查配置问题
    logger.debug(
        "Created model %s (provider=%s, thinking=%s)",
        model_config.name,
        model_config.provider,
        effective_thinking,
    )
    return model


def _model_to_settings(model_config: ModelConfig) -> dict[str, Any]:
    """把 ModelConfig 转换为模型构造器可接受的参数字典。

    参数：
        model_config: 待转换的模型配置

    返回：
        剔除纯元数据字段后的构造器参数字典
        （保留 model / api_key / base_url / temperature / max_tokens 等）
    """
    # 序列化配置，忽略未设置的可选字段
    data = model_config.model_dump(exclude_none=True)

    # 移除纯元数据字段（构造器不认识、仅用于工厂决策）
    for meta_key in ("name", "provider", "supports_vision", "supports_thinking", "context_window"):
        data.pop(meta_key, None)

    return data


def _build_model(
    model_config: ModelConfig,
    settings: dict[str, Any],
    thinking_enabled: bool,
    **kwargs: Any,
) -> BaseChatModel:
    """按 供应商(provider) 选择策略实现类并实例化。

    参数：
        model_config: 模型配置（判断 provider 与思考支持）
        settings: 合并好的配置 + 覆盖的构造器参数
        thinking_enabled: 是否开启思考模式（工厂已降级，此处必为可用值）
        kwargs: 额外参数
    """

    # 获取模型提供商
    provider = model_config.provider

    # 从注册表按 provider 取对应策略
    strategy = get_strategy(provider)

    # 命中策略则委托其实例化（各 provider 细节封装在策略内部）
    if strategy is not None:
        return strategy.build(model_config, settings, thinking_enabled, **kwargs)

    # 不支持的供应商直接抛错
    raise ValueError(
        f"不支持的模型提供方: {provider!r}（仅支持 {' / '.join(supported_providers())}）"
    ) from None
