from __future__ import annotations

import json
import logging

from langchain.tools import tool

from harness.config import get_app_config

"""网页搜索工具

    职责：基于 Tavily 的网页搜索，固定返回最多 5 条（title / url / snippet）
        - 密钥取 tools.web_search.api_key，未配则回退环境变量 TAVILY_API_KEY
        - TavilyClient 懒导入，避免模块加载期触发网络依赖

    对外暴露：
        - web_search_tool / MAX_RESULTS
"""

logger = logging.getLogger(__name__)

MAX_RESULTS = 5


def _get_tavily_client():
    """构建 TavilyClient（懒导入，避免模块加载时触发网络/配置依赖）。"""
    # 懒导入 SDK：避免模块加载期就依赖 tavily 包
    from tavily import TavilyClient

    config = get_app_config().tools.web_search
    # 未配 key 传 None，让 SDK 自己回退环境变量 TAVILY_API_KEY
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
    # 1.搜索；异常转成可读错误串，不抛给模型
    client = _get_tavily_client()
    try:
        res = client.search(query, max_results=MAX_RESULTS)
    except Exception as exc:
        logger.warning("web_search 调用失败: %s", exc)
        return f"错误：搜索失败（{type(exc).__name__}: {exc}）"

    # 2.只保留 title / url / snippet 三个字段，减少 token
    normalized = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in (res.get("results") or [])
    ]
    # 3.无结果给一句可操作的提示
    if not normalized:
        return "没有搜索到相关结果，可以尝试更换措辞。"
    # 4.以 JSON 返回，模型好解析
    return json.dumps(normalized, indent=2, ensure_ascii=False)
