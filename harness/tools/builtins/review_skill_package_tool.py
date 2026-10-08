from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.config.skills import SkillsConfig
from harness.agents.middlewares.input_sanitization_middleware import \
    neutralize_untrusted_tags
from harness.skills.review.analyzer import analyze_skill_package
from harness.skills.review.models import stable_json_dumps
from harness.skills.review.readers import (
    InstalledSkillReader,
    LocalDirectoryReader,
)
from harness.skills.review.renderer import build_static_report, render_report_markdown
from harness.tools.types import Runtime


"""技能包审查工具

    职责：对技能包做确定性检查，产出结构化报告（facts + 语义制品 + 静态报告）交 LLM 判断
        - 两种审查对象：skill://public/<name> 已安装技能、授权目录内的本地技能包
        - 不直接 read_file：防技能内藏恶意指令、省 token、先跑硬规则
        - 回给模型的内容统一净化，防止技能内容冒充系统指令

    对外暴露：
        - review_skill_package   工具本体
"""

_MAX_SEMANTIC_ARTIFACT_CHARS = 80_000


_SCOPE = ["all"]


@tool("review_skill_package", parse_docstring=True)
def review_skill_package(
    target: str,
    runtime: Runtime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """审查一个技能包，返回结构化审查结果（facts + 语义制品 + 静态报告）。

    对技能包做确定性检查（结构规则 + 资源引用 + eval 清单），
    把原文和检查结果一起交给调用方，由 LLM 做语义判断。

    Args:
        target: 审查对象
            - skill://public/<skill-name>  已安装技能
            - 本地目录路径（必须含根 SKILL.md）
    """
    try:
        # 1.取快照（已安装技能 / 本地目录两种来源）
        snapshot = _snapshot_for_target(target, runtime)

        # 2.确定性检查：结构规则 + 资源引用 + eval 清单
        facts = analyze_skill_package(snapshot)

        # 3.挑出语义制品文本（累计字节有上限，超出标 truncated）
        artifacts = _semantic_artifacts(snapshot)

        completed_at = datetime.now(timezone.utc).isoformat()
        static_report = build_static_report(
            facts, scope=_SCOPE, completed_at=completed_at,
        )

        # 4.渲染 markdown：只进 artifact，不进模型可见 content
        markdown = render_report_markdown(static_report, facts)

        payload = {
            "untrusted_review_data": True,
            "facts": facts,
            "artifacts": artifacts,
            "static_report": static_report,
            "markdown": markdown,
        }

        # 5.模型可见 content 只带紧凑子集，并做安全净化
        content_payload = _tool_message_content_payload(payload)
        content = _neutralize_review_content(stable_json_dumps(content_payload))

        return Command(
            update={"messages": [ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                artifact=payload,
            )]},
        )
    # 6.任何异常都转成可读错误消息，不让工具抛栈
    except Exception as exc:
        return Command(
            update={"messages": [ToolMessage(
                content=f"Error: {exc}",
                tool_call_id=tool_call_id,
            )]},
        )



def _snapshot_for_target(target: str, runtime: Runtime) -> dict:
    """按 target 类型分发到不同读取器，产出快照。

    支持两种 target：
        - skill://public/<name>  已安装技能 → InstalledSkillReader
        - 本地目录路径          → LocalDirectoryReader（须通过授权校验）
    """
    # skill:// 走已安装技能读取器；否则按本地目录读取（先过授权校验）
    if target.startswith("skill://"):
        cfg = SkillsConfig()
        return InstalledSkillReader.read_target(
            target,
            skills_root=str(cfg.resolve_skills_root()),
        )

    path = Path(target).expanduser()
    _ensure_local_target_allowed(path, runtime)
    return LocalDirectoryReader(path).read()



def _ensure_local_target_allowed(path: Path, runtime: Runtime) -> None:
    """校验本地审查目标是否在授权目录内，且确实是一个技能包。

    授权目录（删除 /tmp，适配 Windows / 沙箱路径）：
        - 当前工作目录 cwd
        - 技能根目录 SkillsConfig().skills_root
        - 用户当前线程工作区（runtime.state.thread_data.workspace_path）
        - 用户当前线程上传目录（runtime.state.thread_data.uploads_path）

    通过授权校验后，再要求目标必须是"含根 SKILL.md 的目录"，
    防止把普通目录当成技能包审查。
    """
    resolved = path.resolve()

    # 1.授权根：cwd + 技能根（线程的工作区 / 上传目录稍后追加）
    allowed_roots: list[Path] = [
        Path.cwd().resolve(),
        SkillsConfig().resolve_skills_root(),
    ]
    # 2.线程数据取不到就只用前两个根（不阻断审查）
    try:
        thread_data = runtime.state.get("thread_data") or {} if runtime.state else {}
        workspace = thread_data.get("workspace_path")
        uploads = thread_data.get("uploads_path")
        if workspace:
            allowed_roots.append(Path(workspace).resolve())
        if uploads:
            allowed_roots.append(Path(uploads).resolve())
    except Exception:
        pass

    # 3.命中任一授权根即可，但还得确认它确实是个技能包
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        _ensure_local_target_is_package(resolved)
        return

    # 4.都不在授权范围内 → 报错
    raise ValueError(
        "Local review targets must be under the current workspace, "
        "the skills root, or the user's thread workspace/uploads"
    )


def _ensure_local_target_is_package(path: Path) -> None:
    """校验目标是一个技能包：目录且含根 SKILL.md。

    一条规则：目标必须是"目录，且该目录下存在 SKILL.md"。
    普通文件夹 / 散乱文件都不算技能包。
    """
    if path.is_dir() and (path / "SKILL.md").is_file():
        return
    raise ValueError(
        "Local review targets must be directories containing a root SKILL.md"
    )



def _semantic_artifacts(snapshot: dict) -> list[dict]:
    """从快照中挑出语义制品文本发给 LLM。

    制品 = 最能反映技能语义的文件：SKILL.md + references/、templates/、
    evals/ 下的文本文件。累积字节达到 _MAX_SEMANTIC_ARTIFACT_CHARS 后截断。

    每个制品标注 truncated（是否被截断），防止 LLM 把它当全文。
    """
    total = 0
    artifacts: list[dict] = []
    for entry in snapshot.get("files", []):
        path = entry.get("path", "")
        content = entry.get("content")
        # 1.只收文本类文件
        if not path or not content or entry.get("kind") != "text":
            continue
        if not _is_semantic_artifact(path):
            continue

        # 2.累计字节到上限就停
        if total >= _MAX_SEMANTIC_ARTIFACT_CHARS:
            break
        remaining = _MAX_SEMANTIC_ARTIFACT_CHARS - total
        truncated = len(content) > remaining
        text = content[:remaining]
        total += len(text)
        # 3.标注 truncated，防止模型把截断内容当全文
        artifacts.append({
            "path": path,
            "content": text,
            "truncated": truncated,
        })
    return artifacts


def _is_semantic_artifact(path: str) -> bool:
    """判断路径是否属于"语义制品"。

    语义制品 = 描述技能语义的文档类文件：
        - 根 SKILL.md（认知入口）
        - references/、templates/、evals/ 下的文本文件
    脚本、资源、二进制等不属于语义制品（交给安全扫描而非语义阅读）。
    """
    lower = path.lower()
    if path == "SKILL.md":
        return True
    for prefix in ("references/", "templates/", "evals/"):
        if lower.startswith(prefix):
            return True
    return False



def _tool_message_content_payload(payload: dict) -> dict:
    """取模型可见的紧凑子集（去掉预渲染的 markdown）。

    目的：让发给 LLM 的 content 简洁、结构化。
    完整 markdown 渲染保留在 artifact 里，供需要时取用。
    """
    return {
        "untrusted_review_data": payload["untrusted_review_data"],
        "facts": payload["facts"],
        "artifacts": payload["artifacts"],
        "static_report": payload["static_report"],
    }


def _neutralize_review_content(content: str) -> str:
    """对要发给 LLM 的审查内容做安全净化。

    只转义、不包裹（与用户消息的区别）：
    把被审查技能里夹带的 <system-reminder>、<system>、<instruction> 等
    黑名单标签转义成 &lt;system&gt;，防止技能内容冒充系统权威指令
    污染 LLM 上下文；同时中和伪造的用户输入边界标记。
    """
    return neutralize_untrusted_tags(content)
