from __future__ import annotations

from typing import Any, Literal

from harness.skills.review.models import REPORT_SCHEMA_VERSION


"""
    技能包审查报告渲染器。定位：把 analyzer 产出的 facts 渲染成可读报告。

    干两件事：
        - build_static_report：把 facts 换成标准化的 review-report.v1 dict
        - render_report_markdown：把 report dict 渲染成中文 Markdown 字符串

    纯静态渲染，不调用 LLM，所有内容都来自审查的确定性结果。
"""

Readiness = Literal["blocked", "revise", "publish_candidate"]

Assurance = Literal["static_only"]


def _semantic_severity(severity: str) -> str:
    """把 facts 的技术严重级别转成报告中的语义级别。

    blocker → blocker（致命，阻止发布）
    error   → major（严重，需修订）
    warning → minor（轻微，值得注意）
    info    → info
    """
    mapping = {
        "blocker": "blocker",
        "error": "major",
        "warning": "minor",
        "info": "info",
    }
    return mapping.get(severity, severity)


def readiness_from_facts(facts: dict, *, scope: list[str] | None = None) -> str:
    """根据 facts 的 summary 推断技能的"可发布状态"。

    规则：
        blockers > 0        → "blocked"（有致命问题）
        errors > 0          → "revise"（有严重问题）
        否则                → "publish_candidate"
    """
    summary = facts.get("summary", {})
    blockers = int(summary.get("blockers", 0))
    errors = int(summary.get("errors", 0))

    if blockers > 0:
        return "blocked"
    if errors > 0:
        return "revise"

    return "publish_candidate"


def _dimensions_from_facts(facts: dict) -> list[dict]:
    """
    从 facts 确定两个审查维度:
        - structure（结构维度）: blockers 或者 error > 0，有结构性问题
        - evidence_quality（评测证据）: case_count > 0，有评估用例
    """
    summary = facts.get("summary", {})
    blockers = int(summary.get("blockers", 0))
    errors = int(summary.get("errors", 0))

    if blockers > 0:
        structure_status = "blocker"
    elif errors > 0:
        structure_status = "concern"
    else:
        structure_status = "pass"

    evals = facts.get("evals", {})
    case_count = int((evals or {}).get("case_count", 0))
    evidence_status = "pass" if case_count > 0 else "concern"

    return [
        {
            "id": "structure",
            "status": structure_status,
            "summary": (
                "扫描发现了结构性问题，需优先修复。"
                if structure_status == "blocker"
                else "扫描未发现致命结构问题。"
            ),
        },
        {
            "id": "evidence_quality",
            "status": evidence_status,
            "summary": (
                f"发现 {case_count} 个评估用例。"
                if case_count > 0
                else "未发现评估用例，技能正确性难以验证。"
            ),
        },
    ]


def _recommended_actions(facts: dict, readiness: str) -> list[str]:
    """根据 readiness 生成建议动作列表。

    取 findings 里前 5 条的 remediation（修复建议），
    让用户能照着逐步修复问题。
    """
    if readiness == "blocked":
        blockers = [f for f in facts.get("findings", [])
                    if f.get("severity") == "blocker"]
        actions = [f.get("remediation", "") for f in blockers[:5]]
    else:
        actions = [f.get("remediation", "") for f in facts.get("findings", [])[:5]]

    seen: set[str] = set()
    result: list[str] = []
    for a in actions:
        if a and a not in seen:
            seen.add(a)
            result.append(a)
    return result


def build_static_report(
    facts: dict[str, Any],
    *,
    scope: list[str] | None = None,
    reviewer_model: str = "deterministic-review-core",
    completed_at: str | None = None,
) -> dict[str, Any]:
    """把 analyzer 产出的 facts 转成标准化的审查报告 dict。

    facts: analyzer.analyze_skill_package 的返回值
    scope: 审查范围列表（默认 ["all"]）
    reviewer_model: 审查者标识（本项目固定为确定性分析器）
    completed_at: 完成时间戳

    返回 review-report.v1 结构化报告，供 render_report_markdown 渲染。
    """
    scope = scope or ["all"]

    readiness = readiness_from_facts(facts, scope=scope)
    dimensions = _dimensions_from_facts(facts)

    issues: list[dict] = []
    for f in facts.get("findings", []):
        issues.append({
            "id": f.get("rule_id", "?") ,
            "severity": _semantic_severity(str(f.get("severity", "info"))),
            "path": f.get("path"),
            "line": f.get("line"),
            "problem": f.get("message", ""),
            "remediation": f.get("remediation", ""),
        })

    assurance = "static_only"

    limitations = [
        "仅执行静态确定性审查，未进行行为级验证"
        "（未实际运行技能或触发测试）。",
    ]

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "subject": facts.get("subject", {}),
        "profile": facts.get("profile", "prismworker"),
        "scope": scope,
        "reviewer_model": reviewer_model,
        "completed_at": completed_at,
        "readiness": readiness,
        "assurance": assurance,
        "summary": facts.get("summary", {}),
        "issues": issues,
        "dimensions": dimensions,
        "evals": facts.get("evals", {}),
        "analyzers_report": {
            "completeness": facts.get("completeness", {}),
        },
        "limitations": limitations,
        "recommended_actions": _recommended_actions(facts, readiness),
    }

    return report


def render_report_markdown(
    report: dict[str, Any],
    facts: dict[str, Any] | None = None,
) -> str:
    """
    把审查报告 dict 渲染成人类可读的中文 Markdown 报告。

    Args:
        - report: build_static_report 的静态报告
        - facts: analyzer 对快照确定性审查逻辑加工的产物

    Return Markdown 报告:
        # 技能审查报告

        ## 摘要
        - 审查对象：`skill://public/skill-reviewer`
        - 技能指纹：`sha256:abc123...`
        - 就绪状态：**被阻止**
        - 保证级别：静态审查

        ## 范围与完整性
        - 审查范围：`all`
        - 审查标准：`prismworker`
        （注：因为 facts 的 completeness/not_assessed 都没写入，这一节通常到这里就结束了）

        ## 问题
        - blocker `structure.missing-skill-md` at `?`: Skill package is missing a root SKILL.md.
        - blocker `structure.nested-skill-md` at `sub/SKILL.md`: Nested SKILL.md found at: sub/SKILL.md
        - major `structure.invalid-name` at `SKILL.md:1`: Invalid skill name: Skill Reviewer

        ## 维度审查
        - `structure`: blocker - 扫描发现了结构性问题，需优先修复。
        - `evidence_quality`: concern - 未发现评估用例，技能正确性难以验证。

        ## 证据
        - Facts 完整：True
        - 局限性：
          - 仅执行静态确定性审查，未进行行为级验证（未实际运行技能或触发测试）。

        ## 建议动作
        1. Create a SKILL.md file at the package root with name and description in frontmatter.
        2. Remove the nested SKILL.md or move it to evals/fixtures/ if it is a test fixture.
    """

    readiness_labels = {
        "blocked": "被阻止",
        "revise": "需要修改",
        "publish_candidate": "可发布候选",
    }
    assurance_labels = {
        "static_only": "静态审查",
    }

    readiness = str(report.get("readiness", "revise"))
    assurance = str(report.get("assurance", "static_only"))
    zh_readiness = readiness_labels.get(readiness, readiness)

    lines: list[str] = ["# 技能审查报告", ""]

    subject = report.get("subject", {})
    lines.extend([
        "## 摘要",
        "",
        f"- 审查对象：`{subject.get('display_ref', 'unknown')}`",
        f"- 技能指纹：`{subject.get('package_digest', 'unknown')}`",
        f"- 就绪状态：**{zh_readiness}**",
        f"- 保证级别：{assurance_labels.get(assurance, assurance)}",
        "",
    ])

    lines.extend(["## 范围与完整性", ""])
    lines.append(f"- 审查范围：`{', '.join(report.get('scope', ['all']))}`")
    lines.append(f"- 审查标准：`{report.get('profile', 'prismworker')}`")

    if facts:
        completeness = facts.get("completeness", {})
        not_assessed = facts.get("not_assessed", [])
        if not_assessed:
            lines.append(
                f"- 未评估项：`{', '.join(str(n) for n in not_assessed)}`"
            )
        elif completeness:
            lines.append(
                f"- 完整度：包已完全枚举"
                f"（{completeness.get('enumerated', 0)} 文件）"
            )
    lines.append("")

    lines.extend(["## 问题", ""])
    issues = report.get("issues", [])
    if not issues:
        lines.append("- 未发现确定性问题。")
    else:
        for issue in issues:
            loc = issue.get("path") or "?"
            if issue.get("line") is not None:
                loc = f"{loc}:{issue['line']}"
            lines.append(
                f"- {issue['severity']} `{issue['id']}` at `{loc}`: "
                f"{issue['problem']}"
            )
    lines.append("")

    lines.extend(["## 维度审查", ""])
    dimensions = report.get("dimensions", [])
    if not dimensions:
        lines.append("- 无维度信息。")
    else:
        for dim in dimensions:
            lines.append(
                f"- `{dim.get('id')}`: {dim.get('status')} - {dim.get('summary')}"
            )
    lines.append("")

    lines.extend(["## 证据", ""])
    lines.append(f"- Facts 完整：{bool(facts) if facts else '未提供'}")
    limitations = report.get("limitations", [])
    if limitations:
        lines.append("- 局限性：")
        for lim in limitations:
            lines.append(f"  - {lim}")
    lines.append("")

    lines.extend(["## 建议动作", ""])
    actions = report.get("recommended_actions", [])
    if not actions:
        lines.append("- 无待办动作。")
    else:
        for i, action in enumerate(actions, start=1):
            lines.append(f"{i}. {action}")
    lines.append("")

    return "\n".join(lines)