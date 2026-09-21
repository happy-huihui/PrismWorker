from __future__ import annotations

from harness.prompt.loader import (
    PromptConfigurationError,
    PromptError,
    PromptNotFound,
    load_chat,
    load_text,
    render_text,
)

"""提示词统一管理层（harness.prompt）

    职责：集中托管全项目「给模型的指令模板」，并提供通用加载/渲染入口。
    原则：本包只放模板文本 + 通用 loader，绝不 import 任何业务子系统；
         各子系统反向依赖本包，自己装配变量再调用 render_text / load_chat。
    模板：templates/ 下按域分子目录，一文件一模板（.md 纯文本 / .chat.yaml 多角色）。

    对外暴露：
        - load_text / render_text   纯文本模板读取与 $var 渲染
        - load_chat                 chat 多角色模板读取与渲染
        - PromptError / PromptNotFound / PromptConfigurationError
"""

__all__ = [
    "load_text",
    "render_text",
    "load_chat",
    "PromptError",
    "PromptNotFound",
    "PromptConfigurationError",
]
