from __future__ import annotations

import re
from pathlib import PurePosixPath

from harness.config.paths import SKILLS_CONTAINER_PREFIX, VIRTUAL_PATH_PREFIX
from harness.skills.package_paths import is_eval_fixture_path
from harness.skills.review.models import make_finding, normalize_relative_path


"""
    技能包资源引用关系图构建。

    检查包内文本文件之间的引用完整性：
    - 引用的文件是否存在（resource.missing）
    - 引用是否越界出包（resource.escaping-link）
    - 资源文件是否无人引用（resource.unreferenced）

    analyzer 在结构检查之后调用此模块，产出资源关系图和检查发现findings，同时将 findings 追加到全局结果。
"""


_MARKDOWN_LINK_RE = re.compile(
    r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)"
)

# 行内代码 span。**必须排除换行**（`[^`\n]+`）：
# 按 CommonMark，行内代码不能跨行；若允许换行，正则会把**围栏代码块**
# 的首尾反引号配成一对 —— 例如
#     ```markdown
#     - 技术栈：[语言 / 框架]
#     ```
# 中 ``` 的前两个反引号互相不成对，于是「第 3 个反引号 … 结束围栏的第 1 个反引号」
# 被当成一个 span，把整块正文吞成「引用路径」，进而报出
# `resource.missing: Referenced resource does not exist: markdown\n- 技术栈：...`
# 这类垃圾 finding（实测：15 个技能里 12 个中招，105 条代码 span 引用中 61 条是误报）。
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")

_PATH_TOKEN_RE = re.compile(
    r"(?<![\w./-])(?:references|scripts|templates|assets|evals)"
    r"/[A-Za-z0-9._~/%+-]+"
)

_RESOURCE_DIRS: frozenset[str] = frozenset({
    "references", "scripts", "templates", "assets", "evals",
})


"""平台刻意挂载的容器内绝对路径前缀。

技能说明书里提到它们**不是**「引用越界」：
    - `/mnt/user-data`  沙箱工作区（产物 / 工作文件的正常去向，沙箱刻意挂载）
    - `/mnt/skills`     技能自身的只读挂载点（说明书引用自带脚本 / 跨技能脚本属正常）
两者都是平台设计的一部分，与「乱指 /etc/passwd」性质不同。
常量统一取自 harness.config.paths，不写死字符串。
"""
_PLATFORM_ABSOLUTE_PREFIXES: tuple[str, ...] = (VIRTUAL_PATH_PREFIX, SKILLS_CONTAINER_PREFIX)


def _is_platform_absolute(ref: str) -> bool:
    """判断绝对路径是否落在平台刻意挂载的前缀下。

    带 `..` 段的一律**不放行**：白名单只管「正当目的地」，
    不管「借道穿越」（如 `/mnt/user-data/../../etc/passwd` 仍按越界报告）。
    前缀必须按段边界匹配，避免 `/mnt/user-datafoo` 这类搭车路径蒙混过关。
    """
    for prefix in _PLATFORM_ABSOLUTE_PREFIXES:
        if ref == prefix or ref.startswith(prefix + "/"):
            return ".." not in PurePosixPath(ref).parts
    return False




def _extract_references(content: str) -> set[str]:
    """从文本内容中提取所有可能的资源引用路径。

    通过三种正则叠加扫描：
    - Markdown 链接/图片语法
    - 行内代码（含 / 的才视为路径）
    - 裸路径 token（以资源目录开头）
    """
    refs: set[str] = set()

    for m in _MARKDOWN_LINK_RE.finditer(content):
        path = m.group(1).split("#", 1)[0]
        if path:
            refs.add(path)

    for m in _CODE_SPAN_RE.finditer(content):
        token = m.group(1)
        if "/" in token:
            refs.add(token)

    for m in _PATH_TOKEN_RE.finditer(content):
        refs.add(m.group(0))

    return refs


def _resolve_reference(source_path: str, raw_ref: str) -> str | None:
    """
    将 路径引用 解析为包内规范路径。
    示例：
        source_path: "/project/docs/guide/index.md"
        raw_ref: "../assets/logo.png"

    :return
        - None 表示应忽略（锚点/URL/空引用）
        - "__ESCAPES__" 表示引用越界出包
        - 否则返回规范化后的路径
    """

    ref = raw_ref.strip().strip("\"'")
    if not ref:
        return None

    # 提取噪音：`///`、` / ` 这类「只剩斜杠」的 token 不是路径，直接丢弃。
    if not ref.strip("/"):
        return None

    if ref.startswith("#"):
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9.+-]*:", ref) or "://" in ref:
        return None

    if ref.startswith("/"):
        # 平台刻意挂载的绝对路径（/mnt/user-data、/mnt/skills）属正当去向，不报越界；
        # 其余绝对路径（如 /etc/passwd）仍按越界报告。
        if _is_platform_absolute(ref):
            return None
        return "__ESCAPES__"

    base = PurePosixPath(source_path).parent
    candidate = (base / ref).as_posix()

    try:
        return normalize_relative_path(candidate)
    except ValueError:
        return "__ESCAPES__"



def build_resource_graph(snapshot: dict) -> tuple[dict, list]:
    """
    构建技能的完整资源引用关系图 graph，产出完整性检查 finding。

    # 第一部分：资源关系图 (graph) =====
    "graph": {
        # 1.nodes: 技能包内所有文件清单（扁平列表）
        "nodes": [
          {
            "path": "SKILL.md",
            "kind": "text"
          },
          ...
        ],
        # 2.edges: 有效的引用边 文件路径 -> 完整引用指向
        "edges": [
          {
            "source": "SKILL.md",           // 引用方（源文件）
            "target": "assets/style.css"    // 被引用方（目标资源）
          },
          ...
        ],
        # 3.orphans: 没有被引用的孤儿资源
        "orphans": [
          "assets/unused_theme.css",
          "images/old_backup.jpg"
        ]
      },

    # 第二部分：完整性检查发现 (findings): 包含三类规则告警
    "findings": [
        // 类型 1: resource.missing —— 引用的文件在包里不存在
        {
          "rule_id": "resource.missing",               // 规则ID
          "severity": "warning",                       // 严重级别
          "path": "assets/missing_script.py",          // 出问题的路径（缺失的目标）
          "message": "Referenced resource does not exist: assets/missing_script.py",
          "remediation": "Add the missing file, fix the reference path, or remove the stale reference."
        }
        ...
      ]
    }
    """

    files: dict[str, dict] = {e["path"]: e for e in snapshot["files"]}

    nodes = [
        {"path": p, "kind": e.get("kind", "unknown")}
        for p, e in files.items()
    ]

    edges: list[dict] = []
    missing: set[str] = set()
    escaping: set[str] = set()
    referenced: set[str] = set()

    for file_path, entry in files.items():
        if entry.get("kind") != "text" or not entry.get("content"):
            continue
        content: str = entry["content"]
        refs = _extract_references(content)

        for ref in refs:
            resolved = _resolve_reference(file_path, ref)
            if resolved is None:
                continue
            if resolved == "__ESCAPES__":
                escaping.add(ref)
                continue
            if resolved in files:
                edges.append({"source": file_path, "target": resolved})
                referenced.add(resolved)
            else:
                missing.add(resolved)

    orphans: list[str] = []
    for path in files:
        first_seg = path.split("/", 1)[0] if "/" in path else path

        if first_seg not in _RESOURCE_DIRS:
            continue
        if path in referenced:
            continue
        if path in ("evals/evals.json", "evals/trigger_eval_set.json"):
            continue
        if is_eval_fixture_path(path):
            continue

        orphans.append(path)

    graph = {
        "nodes": nodes,
        "edges": sorted(edges, key=lambda e: (e["source"], e["target"])),
        "orphans": sorted(orphans),
    }

    findings: list[dict] = []
    for target in sorted(missing):
        findings.append(make_finding(
            rule_id="resource.missing",
            severity="warning",
            path=target,
            message=f"Referenced resource does not exist: {target}",
            remediation="Add the missing file, fix the reference path, "
                        "or remove the stale reference.",
        ))
    for ref in sorted(escaping):
        findings.append(make_finding(
            rule_id="resource.escaping-link",
            severity="warning",
            path=ref,
            message=f"Reference escapes the package boundary: {ref}",
            remediation="Use relative paths within the skill package.",
        ))
    for path in sorted(orphans):
        findings.append(make_finding(
            rule_id="resource.unreferenced",
            severity="warning",
            path=path,
            message=f"Resource is not reachable from SKILL.md or "
                    f"another referenced resource: {path}",
            remediation="Reference it from SKILL.md using a read-when "
                        "directive, or remove it.",
        ))

    return graph, findings