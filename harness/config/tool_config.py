"""工具配置。

管理 Lead Agent 可用工具的开关与参数。目前包含：
    - Web 搜索（tavily 提供）与网页抓取
    - 全局工具白名单（留空表示全部启用）
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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

    enabled_tools: list[str] = Field(default_factory=list, description="工具白名单")