from __future__ import annotations

"""runtime 包（运行编排框架）

    职责：提供「一次 agent run 从提交到流式收尾」的全部框架能力，供 app 层依赖（本层不 import app）
        - events / sse_stream   事件模型 + 进程内发布订阅
        - runs                  run 编排：models / store / worker / manager
        - threads_data          线程会话元数据
        - history               checkpoint → 前端可渲染历史
        - graph                 Lead Agent 装配 + LRU 缓存
        - sandbox               真实沙箱热池运行时
        - serialization / user_context / assembly  序列化、请求级 user_id、组装根

    对外暴露：
        - 本包不 re-export 符号（避免包初始化期导入环），使用方按 harness.runtime.<pkg> 精确导入
"""
