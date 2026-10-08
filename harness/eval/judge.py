from __future__ import annotations

from typing import Any

"""评测裁判模型工厂（judge）——独立低成本非思考模型，避免与业务模型互相污染。

    职责：提供评测专用的「裁判模型」。用独立的低成本非思考模型打 rubric 分，
        (1) 与业务模型不同源，避免自我偏袒 (2) 关思考，避免思维链污染打分。

    对外暴露：
        - create_judge_model   按档名创建 judge 模型（缺省 deepseek-flash）
"""

# 缺省裁判档名：mimo-flash（低成本、非思考、稳定，适合打 rubric）。
# 注意：用哪个 provider 取决于哪个账号有余额，可用 --judge 覆盖；跨 provider 更客观，
# 但当前 DeepSeek 无余额、OpenAI 未配 key，故默认回到 MiMo（与业务模型同 provider，存在自我偏袒，
# 后续有独立 judge 账号再换）。
_DEFAULT_JUDGE = "mimo-flash"


def create_judge_model(name: str | None = None) -> Any:
    """创建评测裁判模型（OpenAI 兼容、thinking 关闭）。

    参数：
        name: 模型档名（如 deepseek-flash）；None 用默认

    返回：
        ChatModel 实例
    """
    from harness.config.app_config import get_app_config
    from harness.models.factory import create_chat_model

    # 1.缺省裁判 = deepseek-flash；显式传名则覆盖
    judge_name = name or _DEFAULT_JUDGE
    # 2.关思考、复用全局配置（模型密钥/地址都走 config.yaml）
    return create_chat_model(
        name=judge_name,
        thinking_enabled=False,
        app_config=get_app_config(),
    )
