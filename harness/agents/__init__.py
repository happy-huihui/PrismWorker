"""Lead Agent 子系统包

    职责：定义 Lead Agent 的装配链路与会话状态
        - 装配：模型 → 工具 → 中间件 → 提示词 → 编译成图
        - 状态：ThreadState（会话状态蓝图）、GoalState（会话目标）

    对外暴露：
        - 本包不 re-export 符号（避免包初始化期导入环），使用方按子包精确导入
        - harness.agents.factory        Lead Agent 单例入口
        - harness.agents.lead_agent     可运行图组装
        - harness.agents.middlewares    中间件组装
        - harness.agents.thread_state   ThreadState（会话状态蓝图）
        - harness.agents.goal_state     GoalState（会话目标）
"""
