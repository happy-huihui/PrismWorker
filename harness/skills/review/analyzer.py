from __future__ import annotations

import re
from pathlib import Path

from harness.skills.frontmatter import (
    ALLOWED_FRONTMATTER_PROPERTIES,
    SkillMarkdownParts,
    split_skill_markdown,
)
from harness.skills.package_paths import is_eval_fixture_skill_md
from harness.skills.review.digest import compute_package_digest
from harness.skills.review.eval_schema import analyze_eval_manifests
from harness.skills.review.models import (
    FACTS_SCHEMA_VERSION,
    ProfileName,
    make_finding,
    sort_findings,
    summarize_findings,
)
from harness.skills.review.resource_graph import build_resource_graph


"""
    技能包确定性分析器。定位：审查流水线的"大脑"。
    snapshot → facts：
        把 readers 产出的 snapshot（快照，含每个文件的路径/内容/类型/哈希）
        做一套纯逻辑（非 LLM）分析，产出 review-facts.v1 事实字典。

    处理链：
        1. 找根 SKILL.md → 解析 frontmatter → 检查 name/description/body 合法性
        2. 禁止嵌套 SKILL.md（eval fixture 下的豁免）
        3. 包级安全检查：符号链接 / 嵌套压缩包 / 隐藏敏感文件
        4. 委托 resource_graph 检查资源引用完整性
        5. 委托 eval_schema 检查评测清单
        6. 计算包指纹 + 汇总 findings

    analyzer 产出的 facts 后续交给 renderer 生成人类可读报告。
"""


_VALID_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MAX_SKILL_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024

_NESTED_ARCHIVE_EXTS: frozenset[str] = frozenset({
    ".zip", ".tar", ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
    ".7z", ".rar", ".whl",
})

_HIDDEN_SENSITIVE_NAMES: frozenset[str] = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc",
})

_PROFILE: ProfileName = "prismworker"


def _valid_skill_name(name: str) -> bool:
    """校验技能名是否符合规范。

    规则：仅允许小写字母 + 数字 + 短横线，如 "my-skill-v2"。
    原因：LLM 通过技能名唯一标识一个技能，必须确定性能被正确引用。

    示例：
        "skill-reviewer"   → True
        "My Skill"         → False（有大写和空格）
        "a" * 65           → False（超长）
    """
    if len(name) > _MAX_SKILL_NAME_LENGTH:
        return False
    return bool(_VALID_NAME_RE.match(name))


def _is_nested_archive(path: str) -> bool:
    """判断文件路径是否以压缩包后缀结尾。

    后缀集：.zip / .tar / .tar.gz / .tgz / .tar.bz2 / .tbz2
            .tar.xz / .txz / .7z / .rar / .whl

    只判断路径后缀，不读文件内容，轻量级检查。
    """
    lower = path.lower()
    for ext in _NESTED_ARCHIVE_EXTS:
        if lower.endswith(ext):
            return True
    return False


def _is_hidden_sensitive_path(path: str) -> bool:
    """判断路径的任一段是否等于隐藏敏感文件名。

    黑名单：.env（环境变量，可能含 API Key）
            .npmrc（npm 仓库 token）
            .pypirc（PyPI 仓库密码）
            .netrc（通用登录凭据）

    示例：
        "scripts/.env"        → True
        "references/guide.md" → False
        "config/.npmrc"       → True
    """
    parts = path.replace("\\", "/").split("/")
    return any(p in _HIDDEN_SENSITIVE_NAMES for p in parts)



def _analyze_skill_md(content: str, findings: list) -> str | None:
    """
    解析根 SKILL.md 的 frontmatter 和 body，检查合法key + name + description + body，错误追加 finding。

    Args:
        - content: 根 SKILL.md 的完整文本内容
        - findings: 外部传入的 findings 列表，本函数会把检查到的问题追加进去

    检查项（按顺序）：
        1. frontmatter 格式 → structure.invalid-frontmatter (blocker)
        2. 未知字段 → structure.unknown-frontmatter-field (warning)
        3. name 缺失 → structure.missing-name (blocker)
        4. name 不合规 → structure.invalid-name (error)
        5. description 缺失 → structure.missing-description (blocker)
        6. description 超长 → structure.description-too-long (error)
        7. body 为空 → structure.empty-body (error)
    """

    parts: SkillMarkdownParts | None
    error: str | None
    parts, error = split_skill_markdown(content)

    if error is not None or parts is None:
        findings.append(make_finding(
            rule_id="structure.invalid-frontmatter",
            severity="blocker",
            path="SKILL.md",
            message=f"Invalid or missing YAML frontmatter in SKILL.md: {error}",
            remediation="Add a valid YAML frontmatter block at the top of SKILL.md "
                        "between --- delimiters.",
        ))
        return None

    metadata = parts.metadata
    body = parts.body

    unknown_fields = set(metadata.keys()) - ALLOWED_FRONTMATTER_PROPERTIES
    for field in sorted(unknown_fields):
        findings.append(make_finding(
            rule_id="structure.unknown-frontmatter-field",
            severity="warning",
            path="SKILL.md",
            message=f"Unknown frontmatter field: {field}",
            remediation=f"Remove the '{field}' field or rename it to an allowed field.",
        ))

    name = metadata.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        findings.append(make_finding(
            rule_id="structure.missing-name",
            severity="blocker",
            path="SKILL.md",
            message="SKILL.md is missing a 'name' field in frontmatter.",
            remediation="Add 'name: your-skill-name' to the frontmatter.",
        ))
        declared_name = None
    elif not _valid_skill_name(name.strip()):
        findings.append(make_finding(
            rule_id="structure.invalid-name",
            severity="error",
            path="SKILL.md",
            message=f"Invalid skill name: {name!r}. "
                    f"Must be lowercase hyphen-case, max {_MAX_SKILL_NAME_LENGTH} chars.",
            remediation="Rename using only lowercase letters, digits, and hyphens.",
        ))
        declared_name = name.strip()
    else:
        declared_name = name.strip()

    description = metadata.get("description")
    if not description or not isinstance(description, str) or not description.strip():
        findings.append(make_finding(
            rule_id="structure.missing-description",
            severity="blocker",
            path="SKILL.md",
            message="SKILL.md is missing a 'description' field in frontmatter.",
            remediation="Add 'description: What this skill does' to the frontmatter.",
        ))
    elif len(description) > _MAX_DESCRIPTION_LENGTH:
        findings.append(make_finding(
            rule_id="structure.description-too-long",
            severity="error",
            path="SKILL.md",
            message=f"Description exceeds {_MAX_DESCRIPTION_LENGTH} characters "
                    f"(got {len(description)}).",
            remediation=f"Shorten the description to at most {_MAX_DESCRIPTION_LENGTH} characters.",
        ))

    if not body or not body.strip():
        findings.append(make_finding(
            rule_id="structure.empty-body",
            severity="error",
            path="SKILL.md",
            message="SKILL.md has no content after the frontmatter.",
            remediation="Add Markdown content after the frontmatter block.",
        ))

    return declared_name




def analyze_skill_package(snapshot: dict) -> dict:
    """分析技能包快照，产出 review-facts.v1 事实字典。

    这是整个审查流程的"大脑"，组织所有确定性检查：
        1. 找根 SKILL.md → 解析 name/description/body
        2. 检查嵌套 SKILL.md
        3. 包级安全检查（symlink / 嵌套压缩包 / 隐藏敏感文件）
        4. 资源引用完整性检查（委托 resource_graph）
        5. eval 清单检查（委托 eval_schema）
        6. 计算包指纹 + 汇总 findings

    snapshot: readers.py 产出的 package_snapshot.v1 快照
    return:   review-facts.v1 事实字典
    {
        "schema_version": "prismworker.skill-review.facts.v1",
        "subject":      {source, display_ref, declared_name, package_digest},
        "profile":      "prismworker",
        "summary":      {blockers, errors, warnings, infos},
        "findings":     [排序后的所有 finding],
        "resources":    {resource_graph 返回的图},
        "evals":        {eval 清单统计},
        "reader_errors": snapshot 透传的读取错误,
        "analyzer_errors": [],
    }
    """

    findings: list[dict] = []
    analyzer_errors: list[dict] = []

    files: dict[str, dict] = {e["path"]: e for e in snapshot["files"]}

    root_skill = files.get("SKILL.md")
    declared_name: str | None = None

    if root_skill is None:
        findings.append(make_finding(
            rule_id="structure.missing-skill-md",
            severity="blocker",
            message="Skill package is missing a root SKILL.md.",
            remediation="Create a SKILL.md file at the package root with "
                        "name and description in frontmatter.",
        ))
    elif root_skill.get("kind") != "text":
        findings.append(make_finding(
            rule_id="structure.skill-md-not-text",
            severity="blocker",
            path="SKILL.md",
            message="SKILL.md is not a UTF-8 text file.",
            remediation="Ensure SKILL.md is a plain text file with UTF-8 encoding.",
        ))
    else:
        content = root_skill.get("content") or ""
        declared_name = _analyze_skill_md(content, findings)

    for path, entry in files.items():
        if path == "SKILL.md":
            continue
        if not path.endswith("/SKILL.md") and path != "SKILL.md":
            continue
        if is_eval_fixture_skill_md(path):
            continue
        findings.append(make_finding(
            rule_id="structure.nested-skill-md",
            severity="blocker",
            path=path,
            message=f"Nested SKILL.md found at: {path}",
            remediation="Remove the nested SKILL.md or move it to evals/fixtures/ "
                        "if it is a test fixture.",
        ))

    for path, entry in files.items():
        if entry.get("kind") == "symlink":
            findings.append(make_finding(
                rule_id="package.symlink",
                severity="warning",
                path=path,
                message=f"Symbolic link found: {path}",
                remediation="Replace the symlink with the actual file content.",
            ))
        if _is_nested_archive(path):
            findings.append(make_finding(
                rule_id="package.nested-archive",
                severity="warning",
                path=path,
                message=f"Nested archive found: {path}",
                remediation="Remove the archive or extract its contents.",
            ))
        if _is_hidden_sensitive_path(path):
            findings.append(make_finding(
                rule_id="package.hidden-sensitive-file",
                severity="warning",
                path=path,
                message=f"Hidden sensitive file found: {path}",
                remediation="Remove the sensitive file or ensure it is not "
                            "included in the skill package.",
            ))

    resources, resource_findings = build_resource_graph(snapshot)
    findings.extend(resource_findings)

    evals, eval_findings = analyze_eval_manifests(snapshot)
    findings.extend(eval_findings)

    package_digest = compute_package_digest(snapshot)
    subject = dict(snapshot.get("subject", {}))
    subject["declared_name"] = declared_name or subject.get("display_ref", "unknown")
    subject["package_digest"] = package_digest

    findings = sort_findings(findings)
    summary = summarize_findings(findings)

    facts = {
        "schema_version": FACTS_SCHEMA_VERSION,
        "subject": subject,
        "profile": _PROFILE,
        "summary": {
            "blockers": summary["blockers"],
            "errors": summary["errors"],
            "warnings": summary["warnings"],
            "infos": summary["infos"],
        },
        "findings": findings,
        "resources": resources,
        "evals": evals,
        "reader_errors": snapshot.get("reader_errors", []),
        "analyzer_errors": analyzer_errors,
    }

    return facts