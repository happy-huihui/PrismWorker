"""app 层（HTTP 网关与 app 专属能力）

    职责：FastAPI 网关、API 路由与 app 层专属核心能力（鉴权 / 上传 / 产物 / 配置）
        - 只依赖 harness，不被 harness 依赖（分层铁律）
        - 本文件不 re-export

    对外暴露：
        - app.api       HTTP 网关与路由
        - app.core      app 专属核心能力
        - app.runner    后端启动入口（python -m app.runner）
"""
