"""app 核心能力包

    职责：app 层专属的业务能力（不属于 harness 通用层）
        - config     配置入口（薄转发 harness）
        - auth       登录鉴权与 token
        - uploads    上传文件落盘与列举
        - artifacts  产物虚拟路径解析

    对外暴露：
        - 使用方按 app.core.<module> 精确导入
"""
