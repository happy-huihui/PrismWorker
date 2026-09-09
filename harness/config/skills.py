from __future__ import annotations

from pathlib import Path

from harness.skills.review.readers import parse_skill_uri


"""
    技能配置管理：
        - 技能根目录路径，可从 app_config 或 .env 注入 skills_root
        - skill:// URI 的解析为本地路径
        
    替代 DeerFlow 中 ~800 行的 SkillStorage 体系，只保留后续，无需改动 readers.py 代码。
"""


class SkillsConfig:
    """技能配置，管理技能根目录和 skill:// URI 的路径解析。"""

    skills_root: str = "./skills"
    """技能包根目录。所有技能包按 {skills_root}/public/<name>/ 组织。"""

    def resolve_skill_to_path(self, target: str) -> Path:
        """将 skill:// URI 解析为本地文件系统路径。

        parse_skill_uri 负责校验 category 必须是 "public"，
        并做路径穿越防护，这里直接拼接结果。

        示例：
            "skill://public/skill-reviewer"
            → Path("./skills/public/skill-reviewer")
        """
        category, rel_path = parse_skill_uri(target)
        return Path(self.skills_root) / category / rel_path