from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from harness.config.tool_config import ArkImageConfig

"""方舟豆包生图客户端

    职责：把方舟 /images/generations 同步接口封成「给提示词 → 拿图片」的纯调用
        - 不掺业务逻辑（落盘 / 登记产物归 tools.py）
        - 错误码映射成中文处置建议，str(exc) 可直接照做
        - 无状态；每次调用自建 httpx 客户端

    对外暴露：
        - ArkImageClient / ArkImageResult / ArkImageError
        - build_client / IMAGES_GENERATIONS_PATH
"""

logger = logging.getLogger(__name__)

"""官方接口路径（挂在 base_url 之后）。"""
IMAGES_GENERATIONS_PATH = "/images/generations"

"""官方允许的 response_format 取值。"""
_RESPONSE_FORMATS = frozenset({"url", "b64_json"})

"""官方错误码 → 中文处置建议。

为什么要这张表：官方返回的 `message` 是一长串英文散文，虽然**信息完整**
（例如 "Your account 2132358246 has not activated the model ... Please
activate the model service in the Ark Console"），但真正的**可执行信号**是
结构化的 `code`（`ModelNotOpen`）。此前客户端只取 `message`、把 `code` 丢了，
于是「模型没开通」这件事要靠模型/用户自己从英文里读出来 —— 这不是「报错是裸
404」，而是「报错丢了机器可读的那一半」。补上 code + 一句中文该怎么办。

维护约定：命中不了的 code 也不会被吞 —— 仍会原样带出 `[code]` 供检索。
"""
_ERROR_HINTS: dict[str, str] = {
    "ModelNotOpen": "该模型未开通。请到火山方舟控制台「开通管理」中开通对应模型后重试。",
    "InvalidEndpointOrModel.NotFound": "模型 ID 不存在或当前账号无权访问（检查模型名是否写错，或该模型是否在此区域提供）。",
    "AuthenticationError": "API Key 无效或已过期，请检查 tools.ark_image.api_key / 环境变量 ARK_API_KEY。",
    "InvalidApiKey": "API Key 无效或已过期，请检查 tools.ark_image.api_key / 环境变量 ARK_API_KEY。",
    "AccessDenied": "账号无权访问该资源（模型未开通或未授权）。",
    "QuotaExceeded": "账户额度不足，请检查方舟账户余额/配额。",
    "RateLimitExceeded": "触发限流，请稍后重试。",
}

"""HTTP 状态码 → 兜底建议（当错误码不在上表时使用）。"""
_STATUS_HINTS: dict[int, str] = {
    401: "鉴权失败：API Key 无效或已过期。",
    403: "无权限：账号可能未开通该模型或未获授权。",
    429: "触发限流或额度不足，请稍后重试。",
}


class ArkImageError(RuntimeError):
    """生图调用失败（网络异常 / 非 2xx / 响应结构不符预期）。

    属性：
        status: HTTP 状态码（网络异常时为 None）
        detail: 官方返回的错误描述（尽力提取，可能为空）
        code:   官方结构化错误码（如 ModelNotOpen），用于机器判断与检索
        hint:   面向人的处置建议（由 code / status 推导，可能为空）

    `str(exc)` 会把上面这些**全部**拼成一段可直接照做的说明：
        生图接口返回 404 [ModelNotOpen] → 该模型未开通。请到火山方舟控制台…
        官方信息：Your account ... has not activated the model ...
    这样调用方只要 `f"{exc}"` 就够，不必记得额外拼 detail / code。
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        code: str = "",
    ) -> None:
        self.status = status
        self.detail = detail
        self.code = code
        # hint 优先按错误码查表，查不到再按 HTTP 状态兜底
        self.hint = _ERROR_HINTS.get(code, "") or _STATUS_HINTS.get(status or 0, "")
        super().__init__(self._compose(message))

    def _compose(self, message: str) -> str:
        """把 message / code / hint / detail 拼成一句话（不含则自动省略）。"""
        # 逐段拼装：基础消息 → [错误码] → 处置建议 → 换行官方原文
        parts = [message]
        if self.code:
            parts.append(f"[{self.code}]")
        if self.hint:
            parts.append(f"→ {self.hint}")
        text = " ".join(parts)
        if self.detail:
            text += f"\n官方信息：{self.detail}"
        return text


@dataclass(slots=True)
class ArkImageResult:
    """一次生图的结果。

    字段：
        urls: 生成的图片 URL 列表（response_format=url 时有值）
            注意官方 URL 有效期约 24 小时，调用方需及时落盘
        b64_list: base64 图片数据列表（response_format=b64_json 时有值）
        model: 实际使用的模型 ID
        created: 官方返回的创建时间戳
        raw: 原始响应体（排查用）
    """

    urls: list[str]
    b64_list: list[str]
    model: str
    created: int | None
    raw: dict[str, Any]


class ArkImageClient:
    """火山方舟豆包生图客户端（同步，阻塞式）。

    无状态；每次调用自建 httpx 客户端（生图是低频长耗时操作，
    连接池复用收益有限，反而避免生命周期管理问题）。
    """

    def __init__(self, config: ArkImageConfig) -> None:
        """初始化。

        参数：
            config: 生图配置（api_key / base_url / model / size / watermark ...）

        异常：
            ArkImageError: api_key 未配置时抛出（早失败，错误信息更直白）
        """
        # 早失败：没有 api_key 连构造都不允许，报错比请求时更直白
        if not config.api_key:
            raise ArkImageError(
                "未配置火山方舟 API Key（tools.ark_image.api_key 或环境变量 ARK_API_KEY）"
            )
        self._config = config

    @property
    def config(self) -> ArkImageConfig:
        """当前配置。"""
        return self._config

    def _endpoint(self) -> str:
        """拼出完整请求地址（容忍 base_url 尾部带不带斜杠）。"""
        # 容忍 base_url 尾部带不带斜杠
        return f"{self._config.base_url.rstrip('/')}{IMAGES_GENERATIONS_PATH}"

    def generate(
        self,
        prompt: str,
        *,
        size: str | None = None,
        model: str | None = None,
        image: str | list[str] | None = None,
        response_format: str | None = None,
        watermark: bool | None = None,
    ) -> ArkImageResult:
        """生成图片（同步阻塞）。

        参数：
            prompt: 提示词（官方建议 ≤300 汉字 / 600 英文单词）
            size: 图像尺寸，覆盖配置（2K / 4K / 2048x2048）
            model: 模型 ID，覆盖配置
            image: 参考图 URL 或 URL 列表（图生图 / 多图融合）
            response_format: url / b64_json，覆盖配置
            watermark: 是否加水印，覆盖配置

        返回：
            ArkImageResult

        异常：
            ArkImageError: 网络失败、非 2xx、或响应里没有可用图片
        """
        import httpx

        # 1.归一 response_format：显式 > 配置 > 默认 url
        config = self._config
        fmt = (response_format or config.response_format or "url").strip()
        if fmt not in _RESPONSE_FORMATS:
            raise ArkImageError(
                f"不支持的 response_format: {fmt!r}（仅支持 {' / '.join(sorted(_RESPONSE_FORMATS))}）"
            )

        # 按官方字段名组装 body；image 为空时不带该字段
        # 2.按官方字段名组装 body（size 官方必填，始终带上）
        payload: dict[str, Any] = {
            "model": model or config.model,
            "prompt": prompt,
            # size 官方必填，这里始终带上
            "size": size or config.size,
            "response_format": fmt,
            "watermark": config.watermark if watermark is None else bool(watermark),
            # 注意：不传 sequential_image_generation —— 部分模型（如 doubao-seedream-5-0-pro）
            # 不支持该参数，传了会 400 [InvalidParameter]；不传时默认即单图模式。
            "stream": False,
        }
        # 3.image 为空时不带该字段
        if image:
            payload["image"] = image

        # 4.鉴权头 + JSON 内容类型
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }

        try:
            # 生图是低频长耗时操作，每次自建客户端即可（连接池复用收益有限）
            with httpx.Client(timeout=config.timeout_seconds) as client:
                resp = client.post(self._endpoint(), json=payload, headers=headers)
        # 网络层异常单独包一层，便于与 HTTP 错误区分
        except httpx.HTTPError as exc:
            raise ArkImageError(f"生图请求失败（网络异常）：{exc}") from exc

        # 非 2xx：抠出官方 code + message 一起带出（只取 message 会丢掉机器可读信号）
        if resp.status_code >= 400:
            code, detail = _extract_error(resp)
            raise ArkImageError(
                f"生图接口返回 {resp.status_code}",
                status=resp.status_code,
                detail=detail,
                code=code,
            )

        # 响应必须是合法 JSON
        try:
            body = resp.json()
        except ValueError as exc:
            raise ArkImageError("生图接口返回的不是合法 JSON", status=resp.status_code) from exc

        result = _parse_response(body)
        # 一张图都没有也算失败
        if not result.urls and not result.b64_list:
            raise ArkImageError("生图接口未返回任何图片数据", status=resp.status_code)
        logger.info(
            "Ark 生图成功：model=%s images=%d",
            result.model,
            len(result.urls) + len(result.b64_list),
        )
        return result


def _extract_error(resp: Any) -> tuple[str, str]:
    """从失败响应里尽力抠出官方的 (错误码, 错误描述)。

    官方错误体形如：
        {"error": {"code": "ModelNotOpen", "message": "...", "param": "", "type": "Not Found"}}
    结构不符时退化为 ("", 纯文本前 500 字)。

    ⚠️ 只取 message 会丢掉唯一可执行的信号（code），所以这里两个都返回。
    """
    try:
        body = resp.json()
    # 非 JSON 响应退化为纯文本前 500 字
    except Exception:  # noqa: BLE001 —— 非 JSON 响应退化为文本
        return "", (resp.text or "")[:500]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "")
            message = error.get("message") or error.get("code") or ""
            return code, str(message)
        if isinstance(body.get("message"), str):
            return str(body.get("code") or ""), body["message"]
    return "", str(body)[:500]


def _extract_error_detail(resp: Any) -> str:
    """只要描述部分（保留此入口，便于单独调用与测试）。"""
    return _extract_error(resp)[1]


def _parse_response(body: Any) -> ArkImageResult:
    """把官方响应体解析成 ArkImageResult。

    参数：
        body: 官方返回的 JSON 字典

    返回：
        ArkImageResult（urls / b64_list 至少一方有值，除非响应确实为空）
    """
    # 1.非 dict 直接给空结果，不抛（调用方按「无图片」处理）
    if not isinstance(body, dict):
        return ArkImageResult(urls=[], b64_list=[], model="", created=None, raw={})

    # 2.逐条收集 url 与 b64_json（两者可能只出现其一）
    urls: list[str] = []
    b64_list: list[str] = []
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
            encoded = item.get("b64_json")
            if isinstance(encoded, str) and encoded:
                b64_list.append(encoded)

    created = body.get("created")
    return ArkImageResult(
        urls=urls,
        b64_list=b64_list,
        model=str(body.get("model") or ""),
        created=created if isinstance(created, int) else None,
        raw=body if isinstance(body, dict) else {},
    )


def build_client(config: ArkImageConfig | None = None) -> ArkImageClient:
    """按全局配置构造客户端（配置缺省从 AppConfig 取）。

    参数：
        config: 显式配置；None 时读 get_app_config().tools.ark_image
    """
    # 未显式给配置就读全局配置
    if config is None:
        from harness.config.app_config import get_app_config

        config = get_app_config().tools.ark_image
    return ArkImageClient(config)
