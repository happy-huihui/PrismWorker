

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

"""审查数据结构与工具

    职责：技能审查系统的字典定义与通用工具，不依赖任何项目内模块
        - 三套 schema 版本号（snapshot / facts / report）
        - 严重级别、Profile、读取围栏等常量
        - 工厂函数：finding / 排序 / 统计 / 确定性序列化 / 路径净化

    对外暴露：
        - PACKAGE_SNAPSHOT_SCHEMA_VERSION / FACTS_SCHEMA_VERSION / REPORT_SCHEMA_VERSION
        - Severity / ProfileName / SEVERITY_RANK / PackageLimits / DEFAULT_PACKAGE_LIMITS
        - stable_json_dumps / normalize_relative_path / make_finding
        - sort_findings / summarize_findings
"""


PACKAGE_SNAPSHOT_SCHEMA_VERSION = "prismworker.skill-package-snapshot.v1"

FACTS_SCHEMA_VERSION = "prismworker.skill-review.facts.v1"

REPORT_SCHEMA_VERSION = "prismworker.skill-review.report.v1"


Severity = Literal["blocker", "error", "warning", "info"]

ProfileName = Literal["prismworker"]

SEVERITY_RANK: dict[str, int] = {
    "blocker": 0,
    "error": 1,
    "warning": 2,
    "info": 3,
}



@dataclass(frozen=True)
class PackageLimits:
    """技能包读取时的安全限制。

    frozen=True：创建后不可修改，防止运行中被误改。
    目的：防止恶意构造的超大技能包（海量文件 / 超大文件）耗尽内存，
          超限的文件会被跳过，并在快照中标记 truncated=True。
    """

    max_files: int = 4096
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024

    def to_dict(self) -> dict[str, int]:
        """转成普通 dict，方便写入快照的 limits 字段。"""
        return {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
        }


DEFAULT_PACKAGE_LIMITS = PackageLimits()



def stable_json_dumps(data: Any) -> str:
    """确定性 JSON 序列化。

    与 json.dumps 的区别：
      sort_keys=True        → 字典键始终按字母序排列
      separators=(",", ":") → 去冒号和逗号后的空格
    效果：同样的输入永远产生完全相同的字节串，方便 diff、缓存、调试。
    """
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


_ABSOLUTE_PATH_RE = re.compile(r"^/?[a-zA-Z]:|^/")
_EMPTY_SEGMENT_RE = re.compile(r"//+")
_TRAVERSAL_RE = re.compile(r"(?:^|/)\.\.(?:/|$)")


def normalize_relative_path(value: str) -> str:
    """
    规范化技能包内部相对路径，并拦截路径穿越。
    这是安全红线，readers 里计算相对路径时都会经过它。
    """

    if not isinstance(value, str):
        raise ValueError(f"Path must be a string, got {type(value).__name__}")

    # 1.统一分隔符并去掉首尾空白
    path = value.replace("\\", "/").strip()
    # 2.空路径无意义
    if not path:
        raise ValueError("Path cannot be empty")
    # 3.绝对路径（含盘符）一律拒
    if _ABSOLUTE_PATH_RE.match(path):
        raise ValueError(f"Path must be relative, got absolute path: {value!r}")

    # 4.折叠重复斜杠并去掉首尾斜杠
    path = _EMPTY_SEGMENT_RE.sub("/", path).strip("/")
    # 5.任何一段是 .. 都拒（安全红线）
    if _TRAVERSAL_RE.search(path):
        raise ValueError(f"Path must not contain '..': {value!r}")

    return path


def make_finding(
    rule_id: str,
    *,
    severity: Severity,
    message: str,
    remediation: str,
    path: str | None = None,
    line: int | None = None,
    source: str = "review-core",
    profile: ProfileName = "prismworker",
    evidence: Any = None,
) -> dict[str, Any]:
    """创建一条标准 finding 的工厂函数。

    参数：
      rule_id      规则编号，如 "structure.missing-name"
      severity     严重级别（blocker/error/warning/info）
      message      问题描述
      remediation  建议修复方式
      path         出问题的文件相对路径（可空）
      line         出问题的行号（可空）
      source       来源标识，review-core 表示来自确定性分析
      profile      使用的审查标准
      evidence     额外证据（可空）

    用工厂函数而非手写 dict：保证所有 finding 结构一致，绝不少字段。
    """
    # 统一字段集合：用工厂保证所有 finding 结构一致、绝不少字段
    return {
        "rule_id": rule_id,
        "source": source,
        "profile": profile,
        "severity": severity,
        "path": path,
        "line": line,
        "message": message,
        "remediation": remediation,
        "evidence": evidence,
    }


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 severity → path → line → rule_id → message 稳定排序。

    保证同样的输入永远产生同样的顺序（确定性输出），
    这样两次审查的结果可比对，不会被列表顺序干扰。
    """
    # 排序键顺序即展示优先级：先严重级别，再定位，最后规则与文案
    return sorted(
        findings,
        key=lambda f: (
            SEVERITY_RANK.get(str(f.get("severity")), SEVERITY_RANK["info"]),
            f.get("path") or "",
            f.get("line") if isinstance(f.get("line"), int) else -1,
            f.get("rule_id") or "",
            f.get("message") or "",
        ),
    )


def summarize_findings(findings: list[dict[str, Any]]) -> dict[str, int]:
    """统计各严重级别的问题数量。

    返回 {"blockers": 0, "errors": 2, "warnings": 1, "infos": 5}
    供 report 的 summary 字段使用，方便一眼看出整体健康状况。
    """
    summary = {"blockers": 0, "errors": 0, "warnings": 0, "infos": 0}
    # rank → 计数字段名的映射（与 SEVERITY_RANK 一一对应）
    rank_to_key = {0: "blockers", 1: "errors", 2: "warnings", 3: "infos"}
    for f in findings:
        severity = str(f.get("severity"))
        rank = SEVERITY_RANK.get(severity, SEVERITY_RANK["info"])
        summary[rank_to_key[rank]] += 1
    return summary
