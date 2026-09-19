from __future__ import annotations

from harness.config.app_config import AppConfig, get_app_config
from harness.config.model_config import ModelConfig

"""模型能力探测

    职责：只查配置里“这个模型支不支持某个能力”，不真正去造模型。
    用途：比如 view_image 工具，只在模型能看图时才挂给 Agent。

    对外暴露：
        - supports_vision     查模型支不支持看图片
        - supports_thinking   查模型支不支持思考模式
        - find_model          按名字（或当前激活）找模型配置，找不到返回 None
"""


def supports_vision(name: str | None = None, *, app_config: AppConfig | None = None) -> bool:
    """查询指定模型是否支持视觉输入。

    用途：view_image 工具只在模型能看图时才注册给 Agent，
    避免给纯文本模型塞一个它用不了的工具。

    参数：
        name: 模型配置名；None 用当前激活模型
        app_config: 显式配置；缺省用全局单例

    返回：
        该模型 supports_vision 是否为 True（找不到模型返回 False）
    """
    # 取配置并定位目标模型
    config = app_config or get_app_config()
    model_config = find_model(config, name)

    # 找不到模型按“不支持”处理
    if model_config is None:
        return False

    # 返回视觉能力开关
    return bool(model_config.supports_vision)


def supports_thinking(name: str | None = None, *, app_config: AppConfig | None = None) -> bool:
    """查询指定模型是否支持思考模式。

    参数：
        name: 模型配置名；None 用当前激活模型
        app_config: 显式配置；缺省用全局单例

    返回：
        该模型 supports_thinking 是否为 True（找不到模型返回 False）
    """
    # 取配置并定位目标模型
    config = app_config or get_app_config()
    model_config = find_model(config, name)

    # 找不到模型按“不支持”处理
    if model_config is None:
        return False

    # 返回思考能力开关
    return bool(model_config.supports_thinking)


def find_model(config: AppConfig, name: str | None) -> ModelConfig | None:
    """按名称或激活配置查找模型，找不到返回 None（不抛错，查询场景用）。

    参数：
        config: 应用配置
        name: 模型配置名；None 表示取当前激活模型

    返回：
        匹配的 ModelConfig；未找到或无激活模型时返回 None
    """
    # 指定了名字则按名字精确查找
    if name is not None:
        return config.get_model_config(name)

    # 否则取激活模型；无激活模型时按“找不到”处理
    try:
        return config.active_model_config()
    except ValueError:
        return None
