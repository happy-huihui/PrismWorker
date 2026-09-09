from __future__ import annotations

import logging

import httpx
from langchain.tools import tool

from harness.config import WebFetchConfig, get_app_config

"""web_fetch 工具：抓取网页并转换为 Markdown。

按用户确认设计：
    - 统一返回 Markdown（不保留 raw HTML 选项）；
    - 实现为 httpx 抓取 + BeautifulSoup 解析 + markdownify 转 Markdown；
    - 单个响应只保留纯文本大小上限 max_chars（默认 20000），
      防止撑爆模型上下文；
    - 超时 / 响应体上限可配（WebFetchConfig）。
"""

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}


def _get_web_fetch_config() -> WebFetchConfig:
    """读取 web_fetch 配置，单例未初始化时回退默认值。"""
    try:
        return get_app_config().tools.web_fetch
    except Exception:
        return WebFetchConfig()


@tool("web_fetch", parse_docstring=True)
def web_fetch_tool(url: str) -> str:
    """抓取一个网页，把正文转换为 Markdown 返回。

    什么情况下用：
    - 需要读取某个网页的具体内容、正文、文档时（配合 web_search 使用）。
    - 搜索结果里的某条 url 值得细看时。

    什么情况下不要用：
    - 需要搜索时才用 web_search。
    - 无法访问需要登录、验证码或任何鉴权的页面。

    注意事项：
    - 只抓取用户直接提供、或搜索/抓取结果里出现的 URL，不要猜测 URL。
    - URL 必须带协议头（https://...），不能省略 www 去猜。
    - 返回内容按 Markdown 截断，超长页面只给开头部分。

    Args:
        url: 要抓取的网页地址（完整 URL，含 https:// 协议头）。
    """
    config = _get_web_fetch_config()
    timeout = config.timeout_seconds
    max_chars = config.max_chars

    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("web_fetch 请求失败 %s: %s", url, exc)
        return f"错误：抓取失败（{type(exc).__name__}: {exc}）"

    if len(resp.content) > MAX_RESPONSE_BYTES:
        return f"错误：页面过大（>{MAX_RESPONSE_BYTES // 1024 // 1024}MB），已拒绝抓取。"
    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        return (
            f"错误：该 URL 返回的不是网页（Content-Type: {content_type or '未知'}）。"
            "如为 PDF/图片等文件，请改用其他方式处理。"
        )

    try:
        md_text = _html_to_markdown(resp.text)
    except Exception as exc:
        logger.warning("web_fetch 解析失败 %s: %s", url, exc)
        return f"错误：页面解析失败（{type(exc).__name__}: {exc}）"

    if len(md_text) > max_chars:
        md_text = md_text[:max_chars] + f"\n\n...[内容过长已截断，共 {len(md_text)} 字符]"
    return md_text


def _html_to_markdown(html_text: str) -> str:
    """把 HTML 转成干净的 Markdown 文本。

    步骤：
        1. BeautifulSoup 解析并提取 <title>（若有，作为一级标题）；
        2. 移除无意义节点（script/style/nav/iframe/footer 等）；
        3. 取 body 区域（兜底回退整页）；
        4. markdownify 把 (标题 + body) 转 Markdown。
    """
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "iframe", "footer", "noscript", "template"]):
        tag.decompose()

    parts: list[str] = []
    title = soup.find("title")
    if title and title.get_text(strip=True):
        parts.append(f"# {title.get_text(strip=True).strip()}")
    body = soup.find("body") or soup
    if body is not None:
        parts.append(str(body))

    if not parts:
        return ""
    md_text = markdownify("\n".join(parts), heading_style="ATX", strip=["img"])
    return _collapse_blank_lines(md_text)


def _collapse_blank_lines(text: str) -> str:
    """把连续多个空行压缩为单个空行（markdownify 输出常见冗余空行）。"""
    import re

    return re.sub(r"\n{3,}", "\n\n", text)