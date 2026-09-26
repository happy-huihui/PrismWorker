from __future__ import annotations

from pydantic import BaseModel, Field

"""工具配置

    职责：管理 Lead Agent 可用工具的开关与参数，目前涵盖三块：
         - Web 搜索（tavily 提供）与网页抓取
         - 火山方舟豆包生图（Ark Image / doubao-seedream）
         - 全局工具白名单（留空表示全部启用）

    对外暴露：
        - WebSearchConfig   web 搜索参数
        - WebFetchConfig    网页抓取参数
        - ArkImageConfig    豆包生图参数
        - ToolConfig        单个工具的开关
        - ToolsConfig       全部工具配置聚合
"""


class WebSearchConfig(BaseModel):
    """Web 搜索配置（tavily 提供）。"""

    enabled: bool = Field(default=True, description="是否启用 web 搜索")

    api_key: str | None = Field(default=None, description="tavily API 密钥")

    max_results: int = Field(default=5, description="搜索结果条数上限")


class WebFetchConfig(BaseModel):
    """网页抓取配置。"""

    enabled: bool = Field(default=True, description="是否启用网页抓取")

    max_chars: int = Field(default=20000, description="抓取正文最大字符数")

    timeout_seconds: int = Field(default=20, description="抓取超时(秒)")


class ArkImageConfig(BaseModel):
    """火山方舟豆包生图配置（Ark Image / doubao-seedream）。

    走方舟 OpenAI 兼容的同步生图接口：POST {base_url}/images/generations，
    Authorization: Bearer {api_key}。

    字段分两类：
        - 官方参数：model / size / watermark / response_format，原样透传
        - 工具行为参数（本项目自加）：落盘文件名前缀、下载超时
    """

    enabled: bool = Field(default=True, description="是否启用生图工具")

    api_key: str | None = Field(default=None, description="火山方舟 API Key（ARK_API_KEY）")

    base_url: str = Field(
        default="https://ark.cn-beijing.volces.com/api/v3",
        description="方舟 OpenAI 兼容接口根地址",
    )

    model: str = Field(
        default="doubao-seedream-4-0-250828",
        description="生图模型 ID（doubao-seedream-*-*）",
    )

    size: str = Field(default="2K", description="图像尺寸：2K / 4K / 2048x2048")

    watermark: bool = Field(
        default=False,
        description="是否加 AI 生成水印（官方默认 true，这里显式关掉）",
    )

    response_format: str = Field(
        default="url",
        description="返回格式：url（默认，24h 有效） / b64_json",
    )

    timeout_seconds: int = Field(default=120, description="单次生图请求超时(秒)")

    filename_prefix: str = Field(
        default="image",
        description="落盘文件名前缀（最终形如 image-20260923-153000.png）",
    )


class ToolConfig(BaseModel):
    """单个工具的开关配置。"""

    name: str = Field(..., description="工具名称")

    enabled: bool = Field(default=True, description="工具开关")


class ToolsConfig(BaseModel):
    """全部工具配置聚合。"""

    web_search: WebSearchConfig = Field(
        default_factory=WebSearchConfig, description="web 搜索配置"
    )

    web_fetch: WebFetchConfig = Field(
        default_factory=WebFetchConfig, description="网页抓取配置"
    )

    ark_image: ArkImageConfig = Field(
        default_factory=ArkImageConfig, description="火山方舟豆包生图配置"
    )

    # 空列表 = 不限制，全部启用
    enabled_tools: list[str] = Field(default_factory=list, description="工具白名单")
