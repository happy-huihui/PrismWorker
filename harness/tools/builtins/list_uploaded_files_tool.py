from __future__ import annotations

import logging
import os
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import tool
from langgraph.config import get_config

from harness.agents.middlewares.input_sanitization_middleware import neutralize_untrusted_tags
from harness.config.paths import get_paths
from harness.runtime.user_content import resolve_runtime_user_id
from harness.tools.types import Runtime
from harness.uploads.manager import is_upload_staging_file
from harness.utils.file_outline import extract_outline_for_file


"""
    列出历史上传文件工具（list_uploaded_files）。

    让 Agent 按需发现之前对话上传过的历史文件。(排除符号连接 + 暂存文件 + .md转换产物)
    用户说"分析我之前上传的那些 PDF"、或刚进一个线程想看看有哪些可用数据时，用它扫描上传目录给出文件清单。

    输出示例（JSON dict）：
    {
      "files": [
        {"filename": "data.csv", 
         "size": 2048,
         "path": "/mnt/user-data/uploads/data.csv", 
         "extension": ".csv"
        },
        {"filename": "report.pdf", 
         "size": 512,
         "path": "/mnt/user-data/uploads/report.pdf", 
         "extension": ".pdf",
         "outline": [{"title": "Introduction", "line": 1}, ...],
         "outline_preview": ["# Report", "", "This is a report about..."]}
      ],
      "total_count": 4,
      "truncated": true,
      "omitted_summary": "1 .md, 1 .png",
      "message": "Found 5 historical file(s)."
    }
"""

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS = 20
_MAX_MAX_RESULTS = 100


def _extension_label(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    return neutralize_untrusted_tags(suffix) or "(no extension)"


def _format_omitted_summary(omitted: list[str]) -> str:
    counts = Counter(_extension_label(Path(f)) for f in omitted)
    parts = [f"{count} {ext}" for ext, count in sorted(counts.items())]
    return neutralize_untrusted_tags(", ".join(parts))


def _resolve_thread_id(runtime: Runtime) -> str | None:
    """解析当前线程 ID（thread_id），从 runtime 上下文或 RunnableConfig 里取。"""

    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id:
        return thread_id
    runtime_config = getattr(runtime, "config", None) or {}
    thread_id = runtime_config.get("configurable", {}).get("thread_id")
    if thread_id:
        return thread_id

    try:
        return get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        return None


def _resolve_user_id(runtime: Runtime) -> str:
    """解析当前用户 ID（user_id）。"""
    return resolve_runtime_user_id(runtime)


def _list_uploaded_files_impl(
    include_outline: bool | list[str] = False,
    max_results: int = _DEFAULT_MAX_RESULTS,
    runtime: Runtime | None = None,
    *,
    _paths: Any | None = None,
) -> dict:
    """核心实现——不依赖 @tool 包装也能独立测试。"""

    if runtime is None:
        return {"files": [], "message": "No runtime context available."}

    thread_id = _resolve_thread_id(runtime)
    if thread_id is None:
        return {"files": [], "message": "Thread not found."}
    user_id = _resolve_user_id(runtime)
    paths = _paths or get_paths()
    uploads_dir = paths.sandbox_uploads_dir(thread_id, user_id=user_id)

    if not uploads_dir.exists():
        return {"files": [], "message": "No uploads directory for this thread."}

    current_run_filenames: set[str] = set()
    try:
        state = runtime.state
        uploaded = state.get("uploaded_files") if isinstance(state, dict) else getattr(state, "uploaded_files", None)
        if isinstance(uploaded, list):
            for entry in uploaded:
                if isinstance(entry, dict) and entry.get("filename"):
                    current_run_filenames.add(entry["filename"])
    except Exception:
        logger.warning(
            "Failed to read uploaded_files from runtime.state; current-run files may appear in list_uploaded_files results",
            exc_info=True,
        )

    max_results = max(1, min(max_results, _MAX_MAX_RESULTS))

    if isinstance(include_outline, bool):
        outline_for_all: bool = include_outline
        outline_filenames: set[str] = set()
    else:
        outline_for_all = False
        outline_filenames = set(include_outline)

    """ 扫描上传目录，收集"真正的历史文件"，并排除本次运行、暂存、以及 .md 转换产物 """

    candidates: list[tuple[float, Path, int]] = []
    try:
        entries = [e for e in os.scandir(uploads_dir) if e.is_file() and not e.is_symlink() and not is_upload_staging_file(e.name)]
        all_names: set[str] = {e.name for e in entries}

        for entry in entries:
            if entry.name in current_run_filenames:
                continue
            if entry.name.endswith(".md"):
                stem = entry.name[:-3]
                non_md_siblings = {n for n in all_names if n != entry.name and Path(n).stem == stem}
                if non_md_siblings:
                    continue
            stat = entry.stat()
            candidates.append((stat.st_mtime, Path(entry.path), stat.st_size))
    except OSError:
        return {"files": [], "message": f"Failed to read uploads directory: {uploads_dir}"}

    """ 判断是否有历史文件；若有，按修改时间倒序排列，再按 max_results 截断 """

    if not candidates:
        return {"files": [], "message": "No historical uploaded files in this thread."}

    candidates.sort(key=lambda item: item[0], reverse=True)
    total_count = len(candidates)
    truncated = total_count > max_results
    visible = candidates[:max_results]
    omitted_paths = [p.name for _, p, _ in candidates[max_results:]]

    """ 遍历最终可见候选，为每个文件构造信息字典；若指定了大纲就提取并加入 """

    files: list[dict] = []
    for _, file_path, st_size in visible:
        filename = file_path.name
        file_info: dict = {
            "filename": neutralize_untrusted_tags(filename),
            "size": st_size,
            "path": neutralize_untrusted_tags(f"/mnt/user-data/uploads/{filename}"),
            "extension": neutralize_untrusted_tags(file_path.suffix),
        }
        should_include_outline = outline_for_all or filename in outline_filenames
        if should_include_outline:
            outline, preview = extract_outline_for_file(file_path)
            if outline:
                file_info["outline"] = [{**entry, "title": neutralize_untrusted_tags(entry["title"])} if "title" in entry else entry for entry in outline]
            if preview:
                file_info["outline_preview"] = [neutralize_untrusted_tags(p) for p in preview]

        files.append(file_info)

    """ 构建最终结果字典 """

    result: dict = {
        "files": files,
        "total_count": total_count,
    }
    if truncated:
        result["truncated"] = True
        result["omitted_summary"] = _format_omitted_summary(omitted_paths)

    if files:
        result["message"] = f"Found {total_count} historical file(s)."
    else:
        result["message"] = "No historical uploaded files in this thread."

    return result


@tool
def list_uploaded_files(
    runtime: Runtime,
    include_outline: Annotated[
        bool | list[str],
        "控制哪些文件返回文档大纲（标题/预览）。"
        "False（默认）：所有文件都不返回大纲，只给文件名、大小、路径；"
        "True：给每个可转 .md 的文件返回大纲/预览；"
        '文件名列表：只给这些特定文件返回大纲/预览（如 ["report.md", "data.csv"]）。',
    ] = False,
    max_results: Annotated[
        int,
        "最多返回多少个文件（默认 20，最大 100）。",
    ] = _DEFAULT_MAX_RESULTS,
) -> dict:
    """发现当前线程里历史上传的文件。

    返回**之前几轮**上传的文件——本次运行上传的文件被排除（它们已在 <current_uploads>）。

    何时用这个工具：
    - 用户提到之前上传过的文件但没指名（如"分析我之前上传的那些 PDF"）
    - 需要查当前线程有哪些可用文件
    - 刚进入一个线程开始干活，想先概览一下可用数据

    何时跳过这个工具：
    - 用户指名了具体文件——直接带路径用 read_file 或 grep
    - 文件是本次运行上传的——它已在 <current_uploads>
    """
    return _list_uploaded_files_impl(
        include_outline=include_outline,
        max_results=max_results,
        runtime=runtime,
    )