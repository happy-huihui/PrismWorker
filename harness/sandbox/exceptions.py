from __future__ import annotations

"""沙箱异常体系

    职责：统一沙箱层的错误类型，异常可携带结构化 details 供上层转成模型友好提示
        - 基类 + 按失败来源细分（连接 / 命令 / 文件 / 路径）
        - 连接类属基础设施错误；命令非零退出不算致命

    对外暴露：
        - SandboxError            异常基类（message + details）
        - SandboxConnectionError  连不上沙箱（容器未起 / 端口错 / 网络不通）
        - SandboxCommandError     容器内命令非零退出
        - SandboxFileError        容器内文件操作失败
        - SandboxPathError        路径越界或含 .. 穿越（继承 SandboxFileError）
"""

class SandboxError(Exception):
    """所有沙箱错误的基类。

    所有子类都可以携带可选 details 字典（结构化上下文），
    供上层中间件/工具转换为对模型友好的错误消息。
    """

    def __init__(self, message: str, details: dict | None = None) -> None:
        # 统一保存一份 message 与结构化 details，供上层拼模型友好提示
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SandboxConnectionError(SandboxError):
    """连不上沙箱。

    触发场景：容器未启动、base_url 端口错误、HTTP 层握手失败。
    这是「基础设施」错误，与用户/模型操作无关，通常需要运维介入。
    """

    def __init__(self, message: str = "无法连接沙箱", base_url: str | None = None, cause: Exception | None = None) -> None:
        # 有 base_url 才放进 details，避免无意义的空字段
        details = {"base_url": base_url} if base_url else None
        super().__init__(message, details)
        self.base_url = base_url
        self.cause = cause


class SandboxCommandError(SandboxError):
    """沙箱内命令执行失败（返回非零退出码）。

    注意：命令执行失败不应该是致命的——把退出码 + 输出交给模型，
    让它自行修正命令。本异常只在「没有拿到任何有效结果」时抛出。
    """

    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        exit_code: int | None = None,
        output: str | None = None,
    ) -> None:
        # 只收录实际提供的字段，缺省的留空不塞 null
        details: dict = {}
        if command is not None:
            details["command"] = command
        if exit_code is not None:
            details["exit_code"] = exit_code
        if output is not None:
            details["output"] = output
        super().__init__(message, details)
        self.command = command
        self.exit_code = exit_code
        self.output = output


class SandboxFileError(SandboxError):
    """沙箱内文件操作失败。

    触发场景：文件不存在、路径越界、权限不足、读写出错。
    """

    def __init__(
        self,
        message: str,
        *,
        path: str | None = None,
        operation: str | None = None,
    ) -> None:
        # 同上：只收录实际提供的字段
        details: dict = {}
        if path is not None:
            details["path"] = path
        if operation is not None:
            details["operation"] = operation
        super().__init__(message, details)
        self.path = path
        self.operation = operation


class SandboxPathError(SandboxFileError):
    """路径不合法：不在 /mnt/user-data 下或存在路径穿越。

    沙箱工具只允许操作 user-data 工作区，任何越界尝试都会被拒绝。
    """

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message, path=path, operation="validate_path")
