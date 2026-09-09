"""app 层配置入口（config）——复用 harness 的 AppConfig，不重复造轮子。

harness.config.app_config 已经提供完整配置能力：models / sandbox / memory /
tools / subagents 子配置聚合、config.yaml 加载链（含 $ENV / ${VAR:default}
环境变量展开）、无文件时的环境变量回退模型、以及进程级全局单例
get_app_config()。app 层（run_service / agent_registry / api 网关）需要的
正是这个被 harness 全链路验证过的配置对象，因此这里只做一层薄转发：

    app.core.config.get_app_config  ──▶  harness.config.app_config.get_app_config

理由：
  1. 双份配置源必然漂移——app 层再造一份解析 = 维护双倍的模型/沙箱/工具
     配置逻辑，得不偿失；
  2. build_lead_agent / AgentRegistry 依赖的就是 harness AppConfig 实例，
     转发保持「同一份配置、同一份模型决策」。

将来若 app 层需要自己特有的运行参数（如 SSE 心跳、缓存容量、展示开关），
应放在 app.api 层或新建 app.core.runtime_settings，不混入本模块。
"""

from __future__ import annotations

from typing import Any

"""
    API：
        get_app_config()   → harness AppConfig 全局单例（懒加载）
        set_app_config(cfg) → 手动注入配置（测试 / 程序化启动）
        reset_app_config()  → 重置单例（测试隔离）
"""


def get_app_config() -> Any:
    """返回 app 层使用的全局配置（转发 harness 单例，懒加载线程安全）。"""
    from harness.config.app_config import get_app_config as _h

    return _h()


def set_app_config(config: Any) -> None:
    """手动设置全局配置（测试或程序化启动时用）。"""
    from harness.config.app_config import set_app_config as _h

    _h(config)


def reset_app_config() -> None:
    """重置全局配置，下次 get_app_config() 重新加载（测试隔离用）。"""
    from harness.config.app_config import reset_app_config as _h

    _h()


def validate_app_config(config: Any | None = None) -> list[str]:
    """校验运行配置，返回问题清单（空列表 = 配置可用）。

    检查项（按严重程度组织）：
      - 致命：models 为空 / active_model 指向不存在的模型 → 服务跑不起来
      - 警告：sandbox 段缺失（进程不带沙箱工具）/ sandbox.image 为空 /
        sandbox.use 非 aio（暂不支持）

    config 缺省取全局单例（runner 启动前调用可传显式对象避免旁路缓存）。
    """
    cfg = config if config is not None else get_app_config()
    problems: list[str] = []

    models = getattr(cfg, "models", None) or []
    if not models:
        problems.append("models 为空：至少需要配置一个模型（复制 config.example.yaml 为 config.yaml 后修改）")
    else:
        active = getattr(cfg, "active_model", None) or ""
        if active:
            matcher = getattr(cfg, "get_model_config", None)
            found = matcher(active) if matcher else None
            if found is None:
                problems.append(f"active_model={active!r} 在 models 列表中不存在")

    sandbox = getattr(cfg, "sandbox", None)
    if sandbox is not None:
        if not getattr(sandbox, "image", ""):
            problems.append("sandbox.image 为空：需指定 all-in-one-sandbox 镜像")
        if getattr(sandbox, "use", "aio") not in ("aio",):
            problems.append(f"sandbox.use={getattr(sandbox, 'use', '')!r} 暂不支持（仅 aio）")
    else:
        problems.append("未配置 sandbox：进程运行时将不挂载沙箱工具（产物交付链路不可用）")

    return problems