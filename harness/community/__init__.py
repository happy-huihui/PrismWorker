"""社区工具包

    职责：聚合第三方能力接入（生图 / 搜索 / 抓取），各自独立子包
        - 本文件不 re-export（避免包初始化期拉起全部子包的重依赖）

    对外暴露：
        - ark_image       火山方舟豆包生图
        - tavily_search   Tavily 网页搜索
        - web_fetch       网页抓取转 Markdown
"""
