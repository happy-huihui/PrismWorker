from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


"""文档大纲提取

    职责：从转换后的 .md 里抽出标题结构（outline），供上传文件清单展示
        - 识别三种标题风格：标准 # 标题、纯加粗结构标题、拆分加粗标题
        - 超过 MAX_OUTLINE_ENTRIES 追加截断哨兵；无标题时退回前几行预览

    对外暴露：
        - extract_outline           从 .md 提取标题列表
        - extract_outline_for_file  按文件找同名 .md，返回 (大纲, 兜底预览)
"""

_BOLD_HEADING_RE = re.compile(r"^\*\*((ITEM|PART|SECTION|SCHEDULE|EXHIBIT|APPENDIX|ANNEX|CHAPTER)\b[A-Z0-9 .,\-]*)\*\*\s*$")

_SPLIT_BOLD_HEADING_RE = re.compile(r"^\*\*[\dA-Z][\d\.]*\*\*\s+\*\*(?!\d[\d\s.,\-–—/:()%]*\*\*)[^*]+\*\*(?:\s+\*\*[^*]+\*\*){0,2}\s*$")

MAX_OUTLINE_ENTRIES = 50

_OUTLINE_PREVIEW_LINES = 5


def _clean_bold_title(raw: str) -> str:
    """清洗标题字符串里 pymupdf4llm 产生的 **xx**加粗残留。

    pymupdf4llm 有时会把相邻加粗 span 输出成 "**A** **B**" 而不是单个
    "**A B**"。这个辅助函数先把这些碎片合并，再去掉最外层的 **...**，
    让调用方拿到纯文本标题。

    示例：
        "**Overview**"                    → "Overview"
        "**UNITED STATES** **SECURITIES**" → "UNITED STATES SECURITIES"
        "plain text"                       → "plain text"（未变化）
    """
    # 先把相邻加粗碎片 **A** **B** 合并成一个 span
    merged = re.sub(r"\*\*\s*\*\*", " ", raw).strip()
    if m := re.fullmatch(r"\*\*(.+?)\*\*", merged, re.DOTALL):
        return m.group(1).strip()
    return merged


def extract_outline(md_path: Path) -> list[dict]:
    """从一个 Markdown 文件提取文档大纲（标题列表）。

    识别 pymupdf4llm 产生的三种标题风格：
      1. 标准 Markdown 标题：以 # 开头的行。行内 **...** 包裹和相邻加粗
         span（** **）会被清洗，得到纯文本标题。
      2. 纯加粗结构标题：**ITEM 1. BUSINESS**、**PART II** 等。SEC 申报用
         同字号的加粗全大写当节标题，pymupdf4llm 无法提升成 # 标题。
      3. 拆分加粗标题：**1** **Introduction**、**3.2** **Attention**。
         章节号和标题文本在底层 PDF 里是分开的 span 时会这样输出。

    参数：
        md_path: .md 文件的路径。

    返回：
        元素为 {title: str, line: int} 的 dict 列表（line 从 1 起）。
        大纲被截断到 MAX_OUTLINE_ENTRIES 时，末尾追加一个哨兵
        {"truncated": True}，供调用方渲染"只展示前 N 个标题"提示，
        不用重新扫文件。
        文件读不了或没有标题时返回空列表。
    """
    outline: list[dict] = []

    try:
        with md_path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue

                # 1.标准 Markdown 标题（# 开头），行内加粗残留交给 _clean_bold_title
                if stripped.startswith("#"):
                    title = _clean_bold_title(stripped.lstrip("#").strip())
                    if title:
                        outline.append({"title": title, "line": lineno})

                # 2.纯加粗结构标题（SEC 申报常见，pymupdf4llm 提不成 # 标题）
                elif m := _BOLD_HEADING_RE.match(stripped):
                    title = m.group(1).strip()
                    if title:
                        outline.append({"title": title, "line": lineno})

                # 3.拆分加粗标题：章节号与标题文本在底层被拆成两个 span
                elif _SPLIT_BOLD_HEADING_RE.match(stripped):
                    title = " ".join(re.findall(r"\*\*([^*]+)\*\*", stripped))
                    if title:
                        outline.append({"title": title, "line": lineno})

                # 4.超过上限：把最后一条换成截断哨兵并停止
                if len(outline) > MAX_OUTLINE_ENTRIES:
                    outline.pop()
                    outline.append({"truncated": True})
                    break
    except Exception:
        return []

    return outline


def extract_outline_for_file(file_path: Path) -> tuple[list[dict], list[str]]:
    """返回文件路径对应文档的大纲 + 兜底内容预览。

    查找由上传转换管线产生的同名 <stem>.md 文件。

    返回：
        (outline, preview) 其中：
        - outline：{title, line} 列表（可能含截断哨兵）。没有标题或没有 .md
          时为空。
        - preview：.md 的前几行非空内容，大纲为空时用作内容锚点，让 Agent
          有点上下文。大纲非空时为空（不需要兜底）。
    """
    # 找同名 .md（上传转换管线的产物）
    md_path = file_path.with_suffix(".md")
    if not md_path.is_file():
        return [], []

    outline = extract_outline(md_path)
    # 有标题就用标题，不再给预览
    if outline:
        logger.debug("Extracted %d outline entries from %s", len(outline), file_path.name)
        return outline, []

    # 无标题才退回前几行非空内容当锚点
    preview: list[str] = []
    try:
        with md_path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    preview.append(stripped)
                if len(preview) >= _OUTLINE_PREVIEW_LINES:
                    break
    except Exception:
        logger.debug("Failed to read preview lines from %s", md_path, exc_info=True)
    return [], preview
