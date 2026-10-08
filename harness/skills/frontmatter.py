from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

"""SKILL.md frontmatter 解析

    职责：把 SKILL.md 切成「头部 YAML frontmatter + 正文 Markdown body」
        - 元数据（name / description 等）在 frontmatter，实际指令在 body
        - 解析失败返回 (None, 错误描述) 而非抛异常（属被审对象的质量问题）

    对外暴露：
        - ALLOWED_FRONTMATTER_PROPERTIES  允许的 frontmatter 字段集
        - SkillMarkdownParts              解析结果容器
        - split_skill_markdown            切分入口
"""

ALLOWED_FRONTMATTER_PROPERTIES: frozenset[str] = frozenset({
    "name",
    "description",
    "license",
    "allowed-tools",
    "required-secrets",
    "secrets-autonomous",
    "metadata",
    "compatibility",
    "version",
    "author",
})


@dataclass(frozen=True)
class SkillMarkdownParts:
    """
    SKILL.md 解析结果的结构化容器
        -  metadata： YAML 解析后的键值对（如 {"name": "my-skill", ...}）
        - frontmatter_text： 原始 YAML 文本（仅用于错误提示）
        - body： 分隔线 --- 之后的所有正文 Markdown

    """
    metadata: dict[str, Any]
    frontmatter_text: str
    body: str


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\n",
    re.DOTALL,
)


def split_skill_markdown(content: str) -> tuple[SkillMarkdownParts | None, str | None]:
    """将 SKILL.md 文本切分为 frontmatter 元数据 + body 正文。

    返回 (SkillMarkdownParts, None) 或 (None, 错误描述)。
    用元组而非抛异常，因为"frontmatter 缺失/格式错误"是审查对象自身
    的质量问题，不是调用方的 bug。
    """
    # 1.去掉 BOM：否则行首的 ^--- 锚点匹配不上
    raw = content.lstrip("\ufeff")

    # 2.必须命中 --- YAML --- 三段结构
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None, "No YAML frontmatter found"

    yaml_text = m.group(1)
    body = raw[m.end():]

    # 3.YAML 解析失败按「被审对象质量差」处理：返回错误而不是抛
    try:
        metadata = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML in frontmatter: {exc}"

    # 4.顶层必须是字典（列表 / 标量都不合法）
    if not isinstance(metadata, dict):
        return None, "Frontmatter must be a YAML dictionary"

    # 5.键统一转 str：YAML 会把纯数字键解析成 int
    metadata = {str(k): v for k, v in metadata.items()}

    return SkillMarkdownParts(metadata=metadata, frontmatter_text=yaml_text, body=body), None
