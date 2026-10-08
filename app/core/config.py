from __future__ import annotations

from typing import Any

"""app 层配置入口

    职责：薄转发 harness 的 AppConfig，不另造一份配置源
        - app 与 harness 共用同一份配置、同一份模型决策
        - 附带启动前的配置校验（致命 / 警告分级）

    对外暴露：
        - get_app_config / set_app_config / reset_app_config
        - validate_app_config   返回问题清单（空 = 可用）
"""

"""
    API：
        get_app_config()   → harness AppConfig 全局单例（懒加载）
        set_app_config(cfg) → 手动注入配置（测试 / 程序化启动）
        reset_app_config()  → 重置单例（测试隔离）
"""


def get_app_config() -> Any:
    """返回 app 层使用的全局配置（转发 harness 单例，懒加载线程安全）。"""
    # 延迟导入 harness 单例，保持 app → harness 单向依赖
    from harness.config.app_config import get_app_config as _h

    return _h()


def set_app_config(config: Any) -> None:
    """手动设置全局配置（测试或程序化启动时用）。"""
    # 测试 / 程序化启动时注入显式配置
    from harness.config.app_config import set_app_config as _h

    _h(config)


def reset_app_config() -> None:
    """重置全局配置，下次 get_app_config() 重新加载（测试隔离用）。"""
    # 清空单例，下次调用重新走加载链
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

    # 1.至少要有模型，否则服务跑不起来
    models = getattr(cfg, "models", None) or []
    if not models:
        problems.append("models 为空：至少需要配置一个模型（复制 config.example.yaml 为 config.yaml 后修改）")
    else:
        # 2.active_model 必须能在 models 里找到
        active = getattr(cfg, "active_model", None) or ""
        if active:
            matcher = getattr(cfg, "get_model_config", None)
            found = matcher(active) if matcher else None
            if found is None:
                problems.append(f"active_model={active!r} 在 models 列表中不存在")

    # 3.sandbox 段缺失只是不挂沙箱工具（警告级）
    sandbox = getattr(cfg, "sandbox", None)
    if sandbox is not None:
        # 4.镜像与 use 是硬性要求（当前仅支持 aio）
        if not getattr(sandbox, "image", ""):
            problems.append("sandbox.image 为空：需指定 all-in-one-sandbox 镜像")
        if getattr(sandbox, "use", "aio") not in ("aio",):
            problems.append(f"sandbox.use={getattr(sandbox, 'use', '')!r} 暂不支持（仅 aio）")
    else:
        problems.append("未配置 sandbox：进程运行时将不挂载沙箱工具（产物交付链路不可用）")

    return problems
