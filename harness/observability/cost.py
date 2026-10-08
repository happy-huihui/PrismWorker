from __future__ import annotations

from typing import Any

"""模型成本计算（cost）——token 用量 → 美元成本的换算。

    职责：
        1.维护各模型每百万 token 的价格表（默认 + config.yaml 覆盖），把一次模型调用的 input/output token 换算成成本，
        2.支持对一组 span 汇总 token 总量与总成本（run 收尾聚合用）。

    对外暴露：
        - lookup_pricing    按模型名查价格（未命中返回 (0, 0)）
        - compute_cost      由 input/output token 算成本
        - summarize_spans   汇总一组 span 的 token 总量与总成本
"""

# 内置默认价格表（每百万 token 美元）：(input, output)，模型标识子串匹配。
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "mimo-v2.6-pro": (0.435, 0.87),
    "mimo-v2.6-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.66, 1.98),
    "deepseek-v4-flash": (0.15, 0.60),
    "gpt-6-astra": (10.00, 50.00),
}

# 成本精度：保留 6 位小数，避免极小成本被四舍五入成 0
_COST_ROUND_DIGITS = 6


def _configured_pricing() -> dict[str, tuple[float, float]]:
    """从 config.yaml 读价格表，读不到/空则回退内置默认价。

    返回：
        {模型标识子串: (input 价, output 价)}；config 未配时用 _DEFAULT_PRICING
    """
    # 1.读观测配置的 pricing 段，转成 (input, output) 二元组
    try:
        from harness.config import get_app_config

        configured = get_app_config().observability.pricing
    except Exception:  # noqa: BLE001 —— 配置缺失/加载失败都回退默认价
        return _DEFAULT_PRICING
    # 2.转成查找表；为空则回退默认价
    table = {key: (item.input, item.output) for key, item in configured.items()}
    return table or _DEFAULT_PRICING


def lookup_pricing(
    model_name: str, pricing: dict[str, tuple[float, float]] | None = None
) -> tuple[float, float]:
    """按模型名查 (input 价, output 价)，未命中返回 (0, 0)（不计成本）。

    参数：
        model_name: 模型标识（如 mimo-v2.6-pro）
        pricing: 显式价格表；None 用 config 价，config 无则内置默认

    返回：
        (每百万 token 输入价, 每百万 token 输出价)，单位美元
    """
    # 1.显式表优先，否则走 config / 默认
    table = pricing if pricing is not None else _configured_pricing()
    if not model_name or not table:
        return (0.0, 0.0)
    # 2.子串匹配：模型名包含表键即命中（如 "mimo-v2.6-pro" 命中 key）
    for key, price in table.items():
        if key in model_name:
            return price
    # 3.全表未命中 → 零价
    return (0.0, 0.0)


def compute_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, tuple[float, float]] | None = None,
) -> float:
    """把一次模型调用的 token 换算成成本（美元）。

    参数：
        model_name: 模型标识
        input_tokens / output_tokens: 本次调用用量
        pricing: 显式价格表；None 用 config / 默认

    返回：
        成本（美元，保留 6 位）；价格未命中或用量为空返回 0.0
    """
    # 1.用量取非负整数，避免异常输入
    in_tokens = max(0, int(input_tokens or 0))
    out_tokens = max(0, int(output_tokens or 0))
    # 2.查价；零价或无用量直接返回 0
    in_price, out_price = lookup_pricing(model_name, pricing)
    if (in_price, out_price) == (0.0, 0.0) or (in_tokens + out_tokens) == 0:
        return 0.0
    # 3.cost = input/1e6 * 输入价 + output/1e6 * 输出价
    cost = (in_tokens / 1_000_000.0) * in_price + (out_tokens / 1_000_000.0) * out_price
    return round(cost, _COST_ROUND_DIGITS)


def summarize_spans(spans: list[dict[str, Any]]) -> dict[str, float]:
    """汇总一组 span 的 token 总量与总成本（run 收尾聚合用）。

    参数：
        spans: span 字典列表（Span.to_dict() 的产物）

    返回：
        {"total_tokens": int, "cost": float}
    """
    total_tokens = 0
    cost = 0.0
    # 1.逐条累加：token 只来自 llm span（工具/检索不产生 token），成本按模型单价算
    for span in spans:
        total_tokens += int(span.get("total_tokens") or 0)
        cost += compute_cost(
            span.get("model_name") or "",
            span.get("input_tokens") or 0,
            span.get("output_tokens") or 0,
        )
    # 2.成本保留 6 位，与 compute_cost 口径一致
    return {"total_tokens": total_tokens, "cost": round(cost, _COST_ROUND_DIGITS)}
