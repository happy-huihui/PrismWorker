from __future__ import annotations

import json
import logging

from langchain.tools import tool

from harness.config import get_app_config

"""web_search 工具：基于 tavily 的网页搜索。

按用户确认设计：
    - 工具名沿用 web_search；
    - 每次搜索固定返回最多 5 条结果（用户确认「固定 5 就行」，不做成可配）；
    - API 密钥从配置 tools.web_search.api_key 读取；未配置时
      TavilyClient 会自动回退环境变量 TAVILY_API_KEY。
"""

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def _get_tavily_client():
    """构建 TavilyClient（懒导入，避免模块加载时触发网络/配置依赖）。"""
    from tavily import TavilyClient

    config = get_app_config().tools.web_search
    api_key = config.api_key or None
    return TavilyClient(api_key=api_key)


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str) -> str:
    """在互联网上搜索信息（返回最多 5 条结果）。

    什么情况下用：
    - 需要查证实时信息、找资料、获取最新动态时。
    - 搜索结果的 title/url/snippet 可帮你判断该点开哪条深入阅读。

    什么情况下不要用：
    - 需要读取某个具体 URL 的内容（用 web_fetch 工具）。

    Args:
        query: 要搜索的关键词或问题描述。
    """
    client = _get_tavily_client()
    try:
        res = client.search(query, max_results=MAX_RESULTS)
    except Exception as exc:
        logger.warning("web_search 调用失败: %s", exc)
        return f"错误：搜索失败（{type(exc).__name__}: {exc}）"

    normalized = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in (res.get("results") or [])
    ]
    if not normalized:
        return "没有搜索到相关结果，可以尝试更换措辞。"
    return json.dumps(normalized, indent=2, ensure_ascii=False)