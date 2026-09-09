from __future__ import annotations

import json

from harness.skills.review.models import make_finding


"""
技能包 eval 清单的结构检查与统计。
    - 一个技能包除了 SKILL.md 和资源文件外，可能还有一个 evals/ 目录，里面放了一些测试用例（给定 query，期望该技能是否被触发）。
    - 本模块对它们做确定性（非模型驱动）检查，不依赖 LLM：
        1. 是不是合法 UTF-8 文本 JSON（不是文本 / JSON 语法坏了 → warning）；
        2. 识别它是哪种 schema 书写风格（versioned / skill-creator-evals / trigger-eval-list / unknown）；
        3. 统计每个清单的正/负触发 case 数量（should_trigger 字段）。
    # evals/trigger_evals.json
    {
      "schema_version": "1.0",
      "cases": [
        {"name": "case1", "query": "...", "should_trigger": true},
        {"name": "case2", "query": "...", "should_trigger": false}
      ]
    }
    - analyzer 在资源图分析之后调用本模块，把 findings 汇总进全局审查结果。
"""



def _classify_manifest(payload: object) -> str:
    """根据 JSON 顶层结构判断 eval 清单属于哪种书写风格。

    返回 versioned / skill-creator-evals / trigger-eval-list / unknown
    """

    if isinstance(payload, dict):
        if isinstance(payload.get("schema_version"), str) \
                and isinstance(payload.get("cases"), list):
            return "versioned"
        if isinstance(payload.get("evals"), list):
            return "skill-creator-evals"
    elif isinstance(payload, list):
        return "trigger-eval-list"

    return "unknown"


def _case_stats(schema: str, cases: list) -> dict:
    """统计一个清单的 case 数量，并按 should_trigger 分正/负触发。

    cases: 由顶层结构取出的 case 列表（dict 或 None）。
    正触发 = should_trigger 为 true；负触发 = should_trigger 为 false。
    统计结果会和 schema 一起塞进该清单的 manifest 记录。
    """

    positive = sum(1 for c in cases if isinstance(c, dict) and c.get("should_trigger") is True)
    negative = sum(1 for c in cases if isinstance(c, dict) and c.get("should_trigger") is False)

    return {
        "schema": schema,
        "case_count": len(cases),
        "positive_trigger_cases": positive,
        "negative_trigger_cases": negative,
    }




def analyze_eval_manifests(
    snapshot: dict,
) -> tuple[dict, list]:
    """提取并检查技能包内所有 evals/*.json 清单。

    return (aggregate, findings)：
        aggregate:  {
            "schema": "versioned"|"skill-creator-evals"|"trigger-eval-list"|"unknown"|"mixed"|None,
            "valid":  全过为 True / 有错为 False / 无清单为 None,
            "case_count": 全部清单 case 总数,
            "positive_trigger_cases": 正触发总数,
            "negative_trigger_cases": 负触发总数,
            "manifests": 每个清单一条 {path, valid, schema, case_count, ...}
        }
        findings:   eval.binary-manifest / eval.invalid-json 两类 warning
    """

    manifests: list[dict] = []
    findings: list[dict] = []
    for entry in snapshot["files"]:
        path: str = entry["path"]
        if not (path.startswith("evals/") and path.endswith(".json")):
            continue

        manifest: dict = {"path": path, "valid": True}

        if entry.get("kind") != "text":
            manifest["valid"] = False
            findings.append(make_finding(
                rule_id="eval.binary-manifest",
                severity="warning",
                path=path,
                message=f"Eval manifest is not a UTF-8 text file: {path}",
                remediation="Store eval manifests as UTF-8 JSON text.",
            ))
            manifests.append(manifest)
            continue

        try:
            payload = json.loads(entry.get("content") or "")
        except json.JSONDecodeError as exc:
            manifest["valid"] = False
            findings.append(make_finding(
                rule_id="eval.invalid-json",
                severity="warning",
                path=path,
                message=f"Eval manifest has invalid JSON: {path}",
                remediation="Fix the JSON syntax or remove the manifest.",
                evidence={"line": exc.lineno, "msg": exc.msg},
            ))
            manifests.append(manifest)
            continue

        schema = _classify_manifest(payload)
        if schema == "skill-creator-evals":
            cases = payload["evals"]
        elif schema == "trigger-eval-list":
            cases = payload
        elif schema == "versioned":
            cases = payload["cases"]
        else:
            cases = []

        manifest.update(_case_stats(schema, cases))
        manifests.append(manifest)

    if not manifests:
        aggregate = {
            "schema": None,
            "valid": None,
            "case_count": 0,
            "positive_trigger_cases": 0,
            "negative_trigger_cases": 0,
            "manifests": [],
        }
        return aggregate, findings

    schemas = {m.get("schema") for m in manifests if m.get("schema")}
    if len(schemas) > 1:
        aggregate_schema = "mixed"
    elif len(schemas) == 1:
        aggregate_schema = next(iter(schemas))
    else:
        aggregate_schema = "unknown"

    aggregate = {
        "schema": aggregate_schema,
        "valid": all(m["valid"] for m in manifests),
        "case_count": sum(m.get("case_count", 0) for m in manifests),
        "positive_trigger_cases": sum(
            m.get("positive_trigger_cases", 0) for m in manifests
        ),
        "negative_trigger_cases": sum(
            m.get("negative_trigger_cases", 0) for m in manifests
        ),
        "manifests": manifests,
    }

    return aggregate, findings