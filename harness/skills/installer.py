from __future__ import annotations

import logging
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

"""技能包安全解压器（installer）——把 .skill（ZIP）归档解到临时目录。

    定位：服务端 Agent 的安装入口第一环。用户上传 .skill 后，先在本模块做
         「受控解压」，再把解压出的技能根目录交给审查（user_skills）。
    安全边界（对照 DeerFlow installer 同款清单）：
        - 拒绝绝对路径 / '..' / 盘符冒号        → 防 zip-slip 目录穿越
        - 跳过 symlink 条目                     → 防符号链接逃逸
        - 检测 ELF/PE/Mach-O 魔数拒可执行文件   → 防恶意二进制
        - 条目数 / 解压总量围栏 + 64KB 流式写   → 防 zip bomb
        - 过滤 macOS 元数据（__MACOSX/.DS_Store）→ 过滤归档噪声
    解压后自动解析技能根目录（唯一子目录，或根下直接是 SKILL.md）。

    对外暴露：
        - MAX_ARCHIVE_ENTRIES / MAX_ARCHIVE_TOTAL_BYTES  围栏常量
        - SkillArchiveError                              归档非法（给 400 用）
        - extract_skill_archive                          解压并返回技能根目录
"""

# 围栏：条目数 / 解压后总字节数（对齐 DeerFlow 默认值）
MAX_ARCHIVE_ENTRIES = 4096
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
# 流式写块大小
_CHUNK_SIZE = 64 * 1024

# 可执行二进制的魔数（ELF / PE / Mach-O 四变体；用完整魔数避免误伤数据文件）
_EXEC_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF",
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
)

# 忽略的 macOS 元数据路径段
_METADATA_NAMES = frozenset({"__MACOSX", ".DS_Store"})


class SkillArchiveError(ValueError):
    """归档非法：扩展名/结构/安全检查不通过（调用方映射为 400）。"""


def extract_skill_archive(archive: Path) -> tuple[Path, Path]:
    """安全解压 .skill 归档到临时目录，返回 (技能根目录, 临时工作目录)。

    参数：
        archive: .skill（ZIP）文件路径

    返回：
        (技能根目录（含根 SKILL.md）, 临时工作目录（调用方用完负责 rmtree）)

    异常：
        SkillArchiveError  扩展名/ZIP 无效/安全检查不通过/结构不合法
    """
    # 1.扩展名与 ZIP 有效性
    if archive.suffix.lower() != ".skill":
        raise SkillArchiveError("只支持 .skill 技能包文件")
    if not zipfile.is_zipfile(archive):
        raise SkillArchiveError("文件不是有效的 ZIP 归档")

    # 2.临时工作目录（调用方负责最终清理）
    workdir = Path(tempfile.mkdtemp(prefix="skill-install-"))
    dest_root = workdir / "extracted"
    dest_root.mkdir(parents=True)

    try:
        _extract_members(archive, dest_root)
        return _resolve_skill_root(dest_root), workdir
    except Exception:
        # 解压失败：清空本次工作目录，不留半截产物
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _extract_members(archive: Path, dest_root: Path) -> None:
    """逐条目安全解压（成员级 + 解析级双层路径校验，流式写 + 围栏）。"""
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        # 条目数围栏（防 zip bomb 的条目维度）
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise SkillArchiveError(f"归档条目数超过上限 {MAX_ARCHIVE_ENTRIES}")

        total_written = 0
        for info in members:
            name = info.filename
            # 1.macOS 元数据与目录条目直接跳过
            if _is_metadata_member(name) or name.endswith("/"):
                continue
            # 2.成员级安全名检查（穿越/绝对路径/盘符）
            rel = _safe_member_rel_path(name)
            # 3.symlink 条目跳过（防符号链接逃逸）
            if stat.S_ISLNK((info.external_attr >> 16) & 0o170000):
                logger.info("跳过归档内的 symlink 条目: %s", name)
                continue

            target = dest_root / rel
            # 4.解析级校验：真实落点必须仍在解压根内（双层防御）
            if not target.resolve().is_relative_to(dest_root.resolve()):
                raise SkillArchiveError(f"归档条目路径越界: {name}")

            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                head = src.read(4)
                if _looks_executable(head):
                    raise SkillArchiveError(f"归档包含可执行文件，已拒绝: {name}")
                total_written += len(head)
                dst.write(head)
                while True:
                    chunk = src.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    # 解压总量围栏（防 zip bomb 的体积维度）
                    if total_written > MAX_ARCHIVE_TOTAL_BYTES:
                        raise SkillArchiveError(
                            f"归档解压总量超过上限 {MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB"
                        )
                    dst.write(chunk)


def _looks_executable(head: bytes) -> bool:
    """判断文件头是否命中可执行魔数（head 实际可能不足 4 字节，双向前缀匹配）。"""
    return any(head.startswith(magic) or magic.startswith(head) for magic in _EXEC_MAGIC)


def _safe_member_rel_path(name: str) -> str:
    """把归档成员名规整为安全的相对路径（非法即抛 SkillArchiveError）。"""
    # 统一分隔符后再切段（Windows 打包者可能用反斜杠）
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise SkillArchiveError(f"归档包含绝对路径条目: {name}")
    if ":" in normalized:
        raise SkillArchiveError(f"归档包含盘符路径条目: {name}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise SkillArchiveError(f"归档包含目录穿越条目: {name}")
    return "/".join(parts)


def _is_metadata_member(name: str) -> bool:
    """是否 macOS 元数据条目（__MACOSX 目录 / .DS_Store）。"""
    normalized = name.replace("\\", "/")
    return any(
        part in _METADATA_NAMES or part == ".DS_Store"
        for part in normalized.split("/")
    )


def _resolve_skill_root(dest_root: Path) -> Path:
    """解析技能根目录：唯一子目录则进入；否则要求根下直接有 SKILL.md。"""
    # 根下直接有 SKILL.md：整包即技能
    if (dest_root / "SKILL.md").is_file():
        return dest_root
    # 过滤元数据后的顶层条目
    children = [p for p in dest_root.iterdir() if p.name not in _METADATA_NAMES]
    dirs = [p for p in children if p.is_dir()]
    # 唯一子目录且其中有 SKILL.md：进入该子目录（打包者多包了一层目录）
    if len(children) == 1 and len(dirs) == 1 and (dirs[0] / "SKILL.md").is_file():
        return dirs[0]
    raise SkillArchiveError("归档中未找到含 SKILL.md 的技能目录")
