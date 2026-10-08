"""技能包审查子系统

    职责：把技能包读成快照、做确定性规则分析、渲染成审查报告
        - readers   读取本地 / skill:// 技能包为快照
        - analyzer  对快照跑确定性规则
        - renderer  生成结构化审查报告
        - resource_graph / eval_schema / digest / models  子能力与数据结构

    对外暴露：
        - 使用方按 harness.skills.review.<module> 精确导入
"""
