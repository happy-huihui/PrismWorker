from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from harness.config.paths import get_paths
from harness.skills.installer import SkillArchiveError, extract_skill_archive
from harness.skills.review.analyzer import analyze_skill_package
from harness.skills.review.readers import LocalDirectoryReader
from harness.skills.review.renderer import readiness_from_facts

logger = logging.getLogger(__name__)

"""用户自定义技能仓库（user_skills）

    职责：管理每用户一份的自定义技能目录——列出、从上传的 .skill（ZIP）安装。
    存储：{base_dir}/users/{user_id}/skills/<name>/，一个子目录一个技能包
         （SKILL.md + 附属文件），与公共技能同构，天然按用户隔离。
    安装链路（全在后端 API 进程内完成，与会话线程无关）：
        上传 .skill → installer 安全解压（zip-slip/symlink/可执行/炸弹围栏）
        → 纯静态审查（LocalDirectoryReader → analyze_skill_package →
        readiness_from_facts，仅 publish_candidate 放行）→ 重名覆盖落位。
    覆盖语义（用户拍板「同名覆盖」，对齐 DeerFlow shadow）：
        - 自定义撞自定义：替换旧目录（原子换入，失败保留旧版）
        - 自定义撞公共：不删公共文件；运行期用户版 shadow 公共版
    默认启用：安装后不在黑名单即生效（黑名单是独立开关，安装不清它）。

    对外暴露：
        - user_skills_dir           用户自定义技能目录路径
        - list_user_skills          列出用户自定义技能
        - install_skill_from_archive 从上传的 .skill 归档审查并安装
"""

# 审查放行的唯一门槛状态（renderer 的三态：blocked / revise / publish_candidate）
_READY_TO_PUBLISH = "publish_candidate"


def user_skills_dir(user_id: str) -> Path:
    """返回用户自定义技能目录（不创建）。"""
    return get_paths().user_dir(user_id) / "skills"


def list_user_skills(user_id: str) -> list[dict[str, str]]:
    """列出用户自定义技能（复用公共技能的扫描规则，延迟导入避免环）。"""
    # 延迟导入：skill_middlewares 依赖本模块，此处不能反向顶层依赖它
    from harness.agents.middlewares.skill_middlewares import scan_skills_dir

    return scan_skills_dir(user_skills_dir(user_id))


def install_skill_from_archive(user_id: str, archive_path: str | Path) -> dict[str, Any]:
    """从上传的 .skill（ZIP）归档审查并安装一个自定义技能。

    参数：
        user_id: 用户
        archive_path: 上传落地的 .skill 临时文件路径（调用方负责删除该文件）

    返回：
        成功：{"installed": True, "skill": {name, description, path}}
        失败：{"installed": False, "readiness": ..., "findings": [...],
               "message": 给用户看的失败原因}

    异常：
        SkillArchiveError 归档非法（调用方映射 400）；OSError 读写失败向上抛
    """
    archive = Path(archive_path)
    workdir: Path | None = None
    try:
        # 1.安全解压到临时目录，拿到技能根与工作目录（用完统一清理）
        try:
            skill_root, workdir = extract_skill_archive(archive)
        except SkillArchiveError as exc:
            return {"installed": False, "readiness": None, "findings": [], "message": str(exc)}

        # 2.纯静态审查三步：读快照 → 确定性分析 → 可发布状态判定
        snapshot = LocalDirectoryReader(skill_root).read()
        facts = analyze_skill_package(snapshot)
        readiness = readiness_from_facts(facts)
        findings = facts.get("findings") or []
        if readiness != _READY_TO_PUBLISH:
            return {
                "installed": False,
                "readiness": readiness,
                "findings": findings,
                "message": f"审查未通过（{readiness}），已放弃安装",
            }

        # 3.技能名取自 frontmatter（缺失/非法在审查中已是 blocker/error，走不到这里）
        name = str((facts.get("subject") or {}).get("declared_name") or "").strip()
        if not name:
            return {
                "installed": False,
                "readiness": readiness,
                "findings": findings,
                "message": "审查结果缺少技能名，已放弃安装",
            }

        # 4.同名覆盖落位（custom 撞 custom 替换；custom 撞 public 由运行期 shadow）
        target_root = user_skills_dir(user_id)
        target = target_root / name
        _place_skill(skill_root, target)

        description = ""
        for entry in list_user_skills(user_id):
            if entry["name"] == name:
                description = entry["description"]
                break
        logger.info("用户 %s 安装自定义技能成功: %s", user_id, name)
        return {
            "installed": True,
            "skill": {"name": name, "description": description, "path": str(target)},
        }
    finally:
        # 5.清理解压临时工作目录（临时 .skill 文件由 API 层负责）
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def _place_skill(skill_root: Path, target: Path) -> None:
    """把审查通过的技能目录落到目标位（同名覆盖，原子换入防半截）。"""
    target_root = target.parent
    target_root.mkdir(parents=True, exist_ok=True)
    tmp_target = target_root / f".{target.name}.installing"
    if tmp_target.exists():
        shutil.rmtree(tmp_target)
    # 先复制到同盘临时目录
    shutil.copytree(skill_root, tmp_target)
    # 原子换入：已有旧版先整体让位（失败时旧版仍在 tmp 前不动，尽量保旧）
    if target.exists():
        old_target = target_root / f".{target.name}.old"
        if old_target.exists():
            shutil.rmtree(old_target)
        os.replace(target, old_target)
        try:
            os.replace(tmp_target, target)
        except OSError:
            os.replace(old_target, target)
            raise
        finally:
            if old_target.exists():
                shutil.rmtree(old_target, ignore_errors=True)
    else:
        os.replace(tmp_target, target)
