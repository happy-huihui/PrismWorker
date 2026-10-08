from __future__ import annotations

"""评测包（eval）——LangSmith 离线/在线评测与 langgraph dev 图导出。

    职责：把「评测」从生产运行时里拆成独立子包，提供 golden 数据集、评测器、
        judge 模型工厂、评测 CLI，以及供 langgraph dev 加载的图导出模块。
        不侵入 app.runner 的生产链路。

    对外暴露（逐批落地，模块级懒加载，避免 import 即装配重图）：
        - graph       langgraph dev 加载的 Lead Agent 图（见 harness.eval.graph）
        - datasets    golden 数据集定义与上传（批 2）
        - evaluators  结果正确性/忠实度/工具选择/轨迹质量评测器（批 2）
        - judge       独立非思考 judge 模型工厂（批 2）
        - __main__    CLI：python -m harness.eval（建数据集 / 跑离线评测）
"""
