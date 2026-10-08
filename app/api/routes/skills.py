from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.api.deps import get_user_id
from harness.agents.middlewares.skill_middlewares import scan_skills_dir
from harness.config.skills import SkillsConfig
from harness.runtime.skill_prefs import get_skill_blacklist_store
from harness.skills.installer import MAX_ARCHIVE_TOTAL_BYTES, SkillArchiveError
from harness.skills.user_skills import install_skill_from_archive, list_user_skills

"""技能路由

    职责：技能清单查询、用户黑名单与 .skill 上传安装
        - GET /skills 合并「公共 + 用户自定义」（同名用户版 shadow 公共版）并合入 blocked
        - PUT /skills/blacklist 全量覆盖写黑名单
        - POST /skills/install 安全解压 → 静态审查（publish_candidate 才放行）→ 装入用户目录

    对外暴露：
        - router
"""

router = APIRouter(prefix="/skills", tags=["skills"])

# 单用户黑名单条数上限（技能总数远小于此，纯防御性约束）
_MAX_BLACKLIST_ITEMS = 128
# 上传流式写块大小
_UPLOAD_CHUNK = 1024 * 1024


class SkillOut(BaseModel):
    """一个技能的对外形态：blocked 为用户开关状态，source 区分公共/自定义。"""

    name: str = Field(description="技能名（frontmatter name，缺省回退目录名）")
    description: str = Field(default="", description="技能描述（frontmatter description）")
    blocked: bool = Field(default=False, description="是否被当前用户关闭（黑名单内）")
    source: Literal["public", "custom"] = Field(default="public", description="技能来源")


class SkillListOut(BaseModel):
    """技能清单响应：按名称排序，同名时用户版 shadow 公共版。"""

    skills: list[SkillOut] = Field(default_factory=list, description="全部技能（含 blocked 标记）")


class SkillBlacklistIn(BaseModel):
    """黑名单保存请求体：全量覆盖，空列表即清空。"""

    blocked: list[str] = Field(
        default_factory=list,
        max_length=_MAX_BLACKLIST_ITEMS,
        description="被关闭的技能 name 列表",
    )


class SkillBlacklistOut(BaseModel):
    """黑名单保存响应：回显规整后的集合。"""

    blocked: list[str] = Field(default_factory=list, description="落盘后的黑名单（去重排序）")


class SkillInstallOut(BaseModel):
    """安装结果：installed=False 时带 readiness/findings/message 供前端展示。"""

    installed: bool = Field(description="是否安装成功")
    readiness: str | None = Field(default=None, description="审查状态：blocked/revise/publish_candidate")
    findings: list[dict[str, Any]] = Field(default_factory=list, description="审查发现（失败时展示）")
    message: str | None = Field(default=None, description="给用户看的失败原因")
    skill: dict[str, Any] | None = Field(default=None, description="安装成功的技能信息")


@router.get("", response_model=SkillListOut)
async def list_skills(user_id: str = Depends(get_user_id)) -> SkillListOut:
    """合并「公共 + 当前用户自定义」技能并合入 blocked 标记（同名用户版优先）。"""
    # 1.先取黑名单，稍后逐条打 blocked 标记
    blocked = get_skill_blacklist_store().load(user_id)
    # 先填公共，再让用户自定义同名 shadow（source 变 custom）
    # 2.先填公共技能
    merged: dict[str, SkillOut] = {}
    for entry in scan_skills_dir(SkillsConfig().public_skills_dir()):
        merged[entry["name"]] = SkillOut(
            name=entry["name"],
            description=entry["description"],
            blocked=entry["name"] in blocked,
            source="public",
        )
    # 用户目录读取失败降级为仅公共，不阻断清单
    # 3.再让用户自定义同名 shadow 公共版（source 变 custom）
    try:
        for entry in list_user_skills(user_id):
            merged[entry["name"]] = SkillOut(
                name=entry["name"],
                description=entry["description"],
                blocked=entry["name"] in blocked,
                source="custom",
            )
    # 4.用户目录读失败降级为仅公共，不阻断清单
    except Exception:  # noqa: BLE001 —— 用户技能目录异常不阻断清单
        pass
    # 5.按名称排序，保证前端列表稳定
    return SkillListOut(skills=sorted(merged.values(), key=lambda s: s.name))


@router.put("/blacklist", response_model=SkillBlacklistOut)
async def save_blacklist(body: SkillBlacklistIn, user_id: str = Depends(get_user_id)) -> SkillBlacklistOut:
    """全量覆盖保存当前用户的技能黑名单（空列表即全部启用）。"""
    blocked = get_skill_blacklist_store().save(user_id, body.blocked)
    return SkillBlacklistOut(blocked=sorted(blocked))


@router.post("/install", response_model=SkillInstallOut)
async def install_skill(
    file: UploadFile = File(...),
    user_id: str = Depends(get_user_id),
) -> SkillInstallOut:
    """接收上传的 .skill（ZIP）归档：安全解压 → 审查 → 同名覆盖装入用户技能目录。"""
    # 1.扩展名校验（防误装普通文件）
    # 1.扩展名校验（防误装普通文件）
    filename = file.filename or ""
    if not filename.lower().endswith(".skill"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只支持 .skill 技能包文件")

    tmp_name: str | None = None
    try:
        # 2.流式落地到临时文件（总量围栏，防超大上传拖垮内存）
        # 2.流式落地到临时文件（边读边累计总量，防超大上传拖垮内存）
        with tempfile.NamedTemporaryFile(suffix=".skill", delete=False) as tmp:
            tmp_name = tmp.name
            total = 0
            while chunk := await file.read(_UPLOAD_CHUNK):
                total += len(chunk)
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"归档超过大小上限 {MAX_ARCHIVE_TOTAL_BYTES // (1024 * 1024)}MB",
                    )
                tmp.write(chunk)

        # 3.解压 + 审查 + 落位是阻塞的 CPU/磁盘活，放线程池不卡事件循环
        # 3.解压 + 审查 + 落位是阻塞的 CPU/磁盘活，放线程池不卡事件循环
        try:
            result = await asyncio.to_thread(install_skill_from_archive, user_id, tmp_name)
        except SkillArchiveError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"安装过程读写失败: {exc}",
            ) from exc
        return SkillInstallOut(
            installed=bool(result.get("installed")),
            readiness=result.get("readiness"),
            findings=result.get("findings") or [],
            message=result.get("message"),
            skill=result.get("skill"),
        )
    finally:
        # 4.无论成败都清理上传临时文件
        # 5.关掉上传流；临时文件能删就删，删不掉也不影响响应
        await file.close()
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
