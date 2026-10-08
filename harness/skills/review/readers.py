from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from harness.skills.review.models import (
    DEFAULT_PACKAGE_LIMITS,
    PACKAGE_SNAPSHOT_SCHEMA_VERSION,
    PackageLimits,
    normalize_relative_path,
)

"""技能包快照读取

    职责：把本地目录 / skill:// 技能包读成统一的 package_snapshot.v1 快照
        - 每个文件记录 path / kind / size / sha256 / content
        - 围栏：文件数 / 单文件 / 总量；超限标记 truncated
        - 符号链接单独登记；越界与读取失败记入 reader_errors

    对外暴露：
        - parse_skill_uri          解析 skill://public/<name>
        - LocalDirectoryReader     本地目录读取器
        - InstalledSkillReader     已安装技能读取器（继承本地读取器）
"""

_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".css", ".csv", ".html", ".js", ".json", ".md",
    ".py", ".sh", ".svg", ".toml", ".ts", ".txt", ".yaml", ".yml",
})



def _sha256(data: bytes) -> str:
    """计算 bytes 的 SHA256 摘要。"""
    return hashlib.sha256(data).hexdigest()


def _decode_text(data: bytes, path: str) -> str | None:
    """ 判断文件内容是否为可读 UTF-8 文本。 """

    # 扩展名不在文本白名单且含 NUL → 视为二进制
    if Path(path).suffix.lower() not in _TEXT_EXTENSIONS and b"\x00" in data:
        return None
    try:
        # 统一换行为 \n：Windows 落盘的 CRLF 会让 frontmatter 正则
        # （要求 \n---\n）失配，导致整个包被误判为无 frontmatter
        # 统一换行：CRLF 会让 frontmatter 的 \n---\n 正则失配
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def _empty_snapshot(subject: dict, limits: PackageLimits) -> dict:
    """创建空的 package_snapshot.v1 dict 快照"""
    return {
        "schema_version": PACKAGE_SNAPSHOT_SCHEMA_VERSION,
        "subject": dict(subject),
        "limits": limits.to_dict(),
        "files": [],
        "truncated": False,
        "reader_errors": [],
    }


def _subject(*, source: str, display_ref: str) -> dict:
    """创建 subject 身份字典。"""
    return {"source": source, "display_ref": display_ref}


def _sort_snapshot(snapshot: dict) -> dict:
    """对快照内的 files 和 reader_errors 排序，保证确定性输出。"""

    # 固定排序，保证同一个包每次产出的快照完全一致
    snapshot["files"].sort(key=lambda e: (e.get("path", ""), e.get("kind", "")))
    snapshot["reader_errors"].sort(key=lambda e: (e.get("path", ""), e.get("code", "")))
    return snapshot




def parse_skill_uri(target: str) -> tuple[str, str]:
    """
    解析 skill:// 格式 URI & 类型必须是 "public"

    示例： "skill://public/skill-reviewer" → ("public", "skill-reviewer")
    """
    raw = target[len("skill://"):]
    category, sep, rel_path = raw.partition("/")

    # 类别只允许 public；其余（custom / user）一律拒
    if not sep or category not in {"public"}:
        raise ValueError(
            f"Invalid skill URI: {target!r}. "
            f"Expected format: skill://public/<skill-name>"
        )
    # 相对路径再过一次安全净化
    return category, normalize_relative_path(rel_path)



class LocalDirectoryReader:
    """递归遍历本地文件夹，产出 package_snapshot.v1 快照。"""

    def __init__(
        self,
        root: Path,
        *,
        subject: dict | None = None,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
    ):
        self.root = root.resolve()
        self.limits = limits
        self.subject = subject or _subject(
            source="local_directory",
            display_ref=str(root),
        )

    def read(self) -> dict:
        """遍历目录，构建快照。"""

        snapshot = _empty_snapshot(self.subject, self.limits)
        root_resolved = self.root
        total_bytes = 0
        file_count = 0

        for current_root, dir_names, file_names in os.walk(root_resolved, followlinks=False):
            current = Path(current_root)

            for d in list(dir_names):
                d_path = current / d
                # 1.符号链接目录：从遍历列表摘掉（不跟进），单独登记
                if d_path.is_symlink():
                    dir_names.remove(d)
                    self._append_symlink(
                        snapshot, d_path, root_resolved, file_count
                    )
                    file_count += 1
                    continue

            for filename in file_names:
                f_path = current / filename

                file_count += 1
                # 2.文件数超围栏：标记截断并立即返回
                if file_count > self.limits.max_files:
                    snapshot["truncated"] = True
                    return _sort_snapshot(snapshot)

                # 3.符号链接文件单独登记
                if f_path.is_symlink():
                    self._append_symlink(snapshot, f_path, root_resolved, file_count)
                    continue

                rel = self._relative(f_path, root_resolved, snapshot)
                if rel is None:
                    continue

                try:
                    st = f_path.stat()
                # 4.stat 失败记 reader_errors 并跳过
                except OSError as exc:
                    snapshot["reader_errors"].append({
                        "path": rel, "code": "stat_failed", "message": str(exc),
                    })
                    continue
                size = st.st_size

                # 5.累计总量超围栏：标记截断并立即返回
                total_bytes += size
                if total_bytes > self.limits.max_total_bytes:
                    snapshot["truncated"] = True
                    return _sort_snapshot(snapshot)

                # 6.单文件超上限：只登记元信息，不读内容
                if size > self.limits.max_file_bytes:
                    snapshot["files"].append({
                        "path": rel, "kind": "binary",
                        "size": size, "sha256": "", "content": None,
                    })
                    continue

                try:
                    data = f_path.read_bytes()
                # 7.读失败记 reader_errors 并跳过
                except OSError as exc:
                    snapshot["reader_errors"].append({
                        "path": rel, "code": "read_failed", "message": str(exc),
                    })
                    continue
                # 8.文本才带 content；二进制只留大小与哈希
                text = _decode_text(data, rel)
                entry: dict = {
                    "path": rel,
                    "kind": "text" if text is not None else "binary",
                    "size": len(data),
                    "sha256": _sha256(data),
                }
                if text is not None:
                    entry["content"] = text
                snapshot["files"].append(entry)

        return _sort_snapshot(snapshot)

    def _append_symlink(
        self,
        snapshot: dict,
        path: Path,
        root: Path,
        file_count: int,
    ) -> None:
        """登记符号链接到快照。"""
        # 超围栏就静默跳过
        if file_count > self.limits.max_files:
            return
        rel = self._relative(path, root, snapshot)
        if rel is None:
            return
        try:
            target = os.readlink(path)
        except OSError:
            return
        snapshot["files"].append({
            "path": rel,
            "kind": "symlink",
            "size": 0,
            # 符号链接没有内容可哈希，用链接目标字符串算
            "sha256": _sha256(target.encode("utf-8")),
            "target": target,
        })

    def _relative(self, path: Path, root: Path, snapshot: dict) -> str | None:
        """计算文件相对 root 的路径，并做安全校验。"""
        try:
            rel = path.relative_to(root)
        # 不在包根之下 → 记错误并跳过
        except ValueError:
            snapshot["reader_errors"].append({
                "path": str(path),
                "code": "outside_root",
                "message": f"File is outside the package root: {path}",
            })
            return None
        # 再过一次路径净化，防 .. 与绝对路径
        try:
            return normalize_relative_path(str(rel))
        except ValueError as exc:
            snapshot["reader_errors"].append({
                "path": str(rel), "code": "invalid_path", "message": str(exc),
            })
            return None



class InstalledSkillReader(LocalDirectoryReader):
    """从 skill:// URI 解析路径，再委托父类读取快照。"""

    @classmethod
    def read_target(
        cls,
        target: str,
        limits: PackageLimits = DEFAULT_PACKAGE_LIMITS,
        skills_root: str = "./skills",
    ) -> dict:
        """解析 URI 并读取快照。

        skills_root: 技能根目录，默认 "./skills"，
                     后续可改为从 SkillsConfig 或 app_config 注入。
        """
        category, rel_path = parse_skill_uri(target)
        # 拼出 {skills_root}/public/<name> 后委托父类读取
        root = Path(skills_root) / category / rel_path
        if not root.exists():
            raise FileNotFoundError(f"Skill not found at: {root}")
        return cls(
            root,
            subject=_subject(source="installed", display_ref=target),
            limits=limits,
        ).read()
