"""技能子系统包

    职责：聚合技能的解析 / 安装 / 用户自定义 / 静态审查能力
        - 本文件不 re-export，使用方按子包精确导入

    对外暴露：
        - frontmatter   SKILL.md frontmatter 解析
        - installer     技能包安全解压
        - user_skills   用户自定义技能
        - review        技能包静态审查
"""
