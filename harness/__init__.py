"""harness 层（后端智能体与运行时）

    职责：LangGraph 智能体、工具、模型、沙箱、技能、记忆与运行编排
        - 不依赖 app 层（分层铁律：app → harness 单向）
        - 本文件不 re-export，使用方按 harness.<pkg> 精确导入

    对外暴露：
        - harness.config       配置加载链与路径管理
        - harness.agents       智能体装配与中间件
        - harness.runtime      run 编排、事件总线、沙箱热池
        - harness.models / tools / sandbox / skills / memory / subagents
"""
