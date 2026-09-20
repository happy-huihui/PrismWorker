from __future__ import annotations

"""runtime 包（运行编排框架）

    职责：把「一次 agent run 从提交到流式收尾」所需的框架能力，按功能分包
         集中于此，供上层 app（HTTP 网关）依赖；本层不 import app。
    结构：
        - events        run 事件模型 + 工具事件路由（当前 run → 总线）
        - sse_stream    进程内发布/订阅总线（无 HTTP；SSE 帧在 app 层）
        - serialization LangChain/state → 朴素结构 与文本/角色/预览工具
        - runs          run 编排：models / store(runs 表) / worker / manager
        - threads_data  线程会话元数据仓库
        - history       checkpoint → 前端可渲染历史
        - user_context  请求级 user_id 解析
        - graph         Lead Agent 装配 + LRU 缓存
        - sandbox       进程级真实沙箱热池运行时
        - assembly      组装根：按配置拼出可运行的 RunManager（lifespan 用）

    说明：为避免包初始化期的导入环，这里不 re-export 子模块符号；
         使用方按 `from harness.runtime.<pkg> import ...` 精确导入。
"""
