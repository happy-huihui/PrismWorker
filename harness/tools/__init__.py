"""工具层

    职责：工具运行时类型的定义处；内置工具与社区工具各自在自己的包里聚合
        - 本文件不 re-export（避免包初始化期拉起全部工具的重依赖）

    对外暴露：
        - Runtime       工具运行时类型（harness.tools.types）
        - 内置工具       harness.tools.builtins
        - 社区工具       harness.community.*
"""
