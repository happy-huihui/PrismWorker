from __future__ import annotations

import os
from pathlib import Path

from harness.config.paths import PROJECT_ROOT, SKILLS_CONTAINER_PREFIX
from harness.skills.review.readers import parse_skill_uri

"""技能配置

    职责：确定技能根目录（宿主侧唯一真源）、容器内挂载点，以及 skill:// URI 的路径解析。

    解析规则：统一走 resolve_skills_root()
        - 默认 {项目根}/skills（项目根由本模块位置反推，与 CWD 无关，
          避免服务从别的目录启动时解析到错误位置）
        - 可用环境变量 PRISM_WORKER_SKILLS_ROOT 覆盖（便于测试与部署）

    对外暴露：
        - PUBLIC_CATEGORY   技能类别目录名
        - SkillsConfig      技能配置
"""

# 项目根由 harness.config.paths 统一提供（见那里的 PROJECT_ROOT 注释）
_PROJECT_ROOT = PROJECT_ROOT

# 覆盖技能根的环境变量名
_SKILLS_ROOT_ENV = "PRISM_WORKER_SKILLS_ROOT"

# 技能类别目录名。与 readers.parse_skill_uri 的校验保持一致（它只接受 "public"），
# 单独抽成常量是为了让「扫描哪一层」这件事有唯一出处。
PUBLIC_CATEGORY = "public"


class SkillsConfig:
    """技能配置，管理技能根目录和 skill:// URI 的路径解析。"""

    skills_root: str = "./skills"
    """技能包根目录（相对路径以项目根为基准）。技能包按 {skills_root}/public/<name>/ 组织。"""

    container_path: str = SKILLS_CONTAINER_PREFIX
    """技能目录在沙箱容器内的挂载点（只读、全线程共享）。"""

    def resolve_skills_root(self) -> Path:
        """把 skills_root 解析成宿主侧绝对路径（技能根的唯一真源）。

        解析顺序：
            1. 环境变量 PRISM_WORKER_SKILLS_ROOT 优先（测试/部署覆盖用）
            2. 绝对路径 → 原样 resolve
            3. 相对路径 → 以**项目根**为基准（不是 CWD，避免服务从别处启动时漂移）

        返回：
            技能根目录的绝对路径
        """
        # 环境变量优先，其次用配置值
        raw = os.getenv(_SKILLS_ROOT_ENV) or self.skills_root
        path = Path(raw).expanduser()
        # 绝对路径直接归一化
        if path.is_absolute():
            return path.resolve()
        # 相对路径一律以项目根为基准
        return (_PROJECT_ROOT / path).resolve()

    def public_skills_dir(self) -> Path:
        """public 类别技能目录 —— 即「每个子目录就是一个技能包」的那一层。

        激活中间件扫描的是这一层（不是 skills_root）：skills_root 下面是类别目录，
        真正的技能包在 {skills_root}/public/<name>/ 里，多一层就会一个技能都扫不到。

        返回：
            {技能根}/public
        """
        return self.resolve_skills_root() / PUBLIC_CATEGORY

    def container_skills_dir(self) -> str:
        """技能目录在容器内的挂载点（去掉尾部斜杠，便于拼接）。"""
        return self.container_path.rstrip("/") or SKILLS_CONTAINER_PREFIX

    def resolve_skill_to_path(self, target: str) -> Path:
        """把 skill:// URI 解析为本地文件系统路径。

        parse_skill_uri 负责校验 category 必须是 "public"，并做路径穿越防护，
        这里只需拼接结果。

        参数：
            target: 形如 skill://public/skill-reviewer 的 URI

        返回：
            Path("{技能根}/public/skill-reviewer")
        """
        # 拆出类别与相对路径（校验与防穿越都在 parse_skill_uri 内）
        category, rel_path = parse_skill_uri(target)
        return self.resolve_skills_root() / category / rel_path
