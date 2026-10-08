"""API 网关包

    职责：FastAPI 应用装配、依赖注入出口、请求 / 响应模型与业务路由
        - deps     依赖注入统一出口
        - main     应用工厂与 lifespan
        - schemas  请求 / 响应模型
        - routes   各业务路由

    对外暴露：
        - app.api.main:app（uvicorn 启动目标）
"""
