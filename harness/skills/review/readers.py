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

""" 
    把技能包从本地文件夹或 skill:// URI 读成统一的 package_snapshot.v1 dict 快照。
    快照含每个文件的 path/kind/size/sha256/content，analyzer 和 digest 都靠它。 
    {
        "schema_version": "prismworker.skill-package-snapshot.v1",
        # 身份标识
        "subject": {
            "source": "installed",          # ← 快照从哪个来源读取的，对应 InstalledSkillReader
            "display_ref": "skill://public/skill-reviewer"  # ← "我叫什么"
        },
        # skill 大小安全围栏
        "limits": {
            "max_files": 4096,              # ← 最多读多少个文件
            "max_file_bytes": 67108864,     # ← 单个文件最大 64MB
            "max_total_bytes": 536870912    # ← 全部文件累计最大 51
        },
        "files": [
            {
                "path": "SKILL.md",          # ← 文件在包里的相对路径
                "kind": "text",              # ← 类型：text / binary / symlink
                "size": 3456,                # ← 文件大小（字节）
                "sha256": "abc123...",       # ← 文件内容的哈希指纹
                "content": "---\n..."        # ← 文件内容，文本文件才有，二进制没有
            },
            ...
        ],
        "truncated": false,                  # ← skill 超限就截断，标记 truncated: true
        "reader_errors": []                  # ← 读取过程中遇到的错误
    }
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

    if Path(path).suffix.lower() not in _TEXT_EXTENSIONS and b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
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

    if not sep or category not in {"public"}:
        raise ValueError(
            f"Invalid skill URI: {target!r}. "
            f"Expected format: skill://public/<skill-name>"
        )
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
                if file_count > self.limits.max_files:
                    snapshot["truncated"] = True
                    return _sort_snapshot(snapshot)

                if f_path.is_symlink():
                    self._append_symlink(snapshot, f_path, root_resolved, file_count)
                    continue

                rel = self._relative(f_path, root_resolved, snapshot)
                if rel is None:
                    continue

                try:
                    st = f_path.stat()
                except OSError as exc:
                    snapshot["reader_errors"].append({
                        "path": rel, "code": "stat_failed", "message": str(exc),
                    })
                    continue
                size = st.st_size

                total_bytes += size
                if total_bytes > self.limits.max_total_bytes:
                    snapshot["truncated"] = True
                    return _sort_snapshot(snapshot)

                if size > self.limits.max_file_bytes:
                    snapshot["files"].append({
                        "path": rel, "kind": "binary",
                        "size": size, "sha256": "", "content": None,
                    })
                    continue

                try:
                    data = f_path.read_bytes()
                except OSError as exc:
                    snapshot["reader_errors"].append({
                        "path": rel, "code": "read_failed", "message": str(exc),
                    })
                    continue
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
            "sha256": _sha256(target.encode("utf-8")),
            "target": target,
        })

    def _relative(self, path: Path, root: Path, snapshot: dict) -> str | None:
        """计算文件相对 root 的路径，并做安全校验。"""
        try:
            rel = path.relative_to(root)
        except ValueError:
            snapshot["reader_errors"].append({
                "path": str(path),
                "code": "outside_root",
                "message": f"File is outside the package root: {path}",
            })
            return None
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
        root = Path(skills_root) / category / rel_path
        if not root.exists():
            raise FileNotFoundError(f"Skill not found at: {root}")
        return cls(
            root,
            subject=_subject(source="installed", display_ref=target),
            limits=limits,
        ).read()