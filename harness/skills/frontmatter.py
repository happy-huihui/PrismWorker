from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

"""
把 SKILL.md 切成两半：头部 YAML frontmatter + 正文 Markdown body。
    - frontmatter 放元数据（name/description 等），
    - body 放技能的实际指令。
整个审查链路靠它起手——没有它，analyzer 无从解析 name/description/body。
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
        - frontmatter：_text 原始 YAML 文本（仅用于错误提示）
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
    raw = content.lstrip("\ufeff")

    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None, "No YAML frontmatter found"

    yaml_text = m.group(1)
    body = raw[m.end():]

    try:
        metadata = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML in frontmatter: {exc}"

    if not isinstance(metadata, dict):
        return None, "Frontmatter must be a YAML dictionary"

    metadata = {str(k): v for k, v in metadata.items()}

    return SkillMarkdownParts(metadata=metadata, frontmatter_text=yaml_text, body=body), None