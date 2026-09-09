"""沙箱配置（Docker all-in-one-sandbox）。

沙箱跑在本机 Docker 里：镜像 all-in-one-sandbox 暴露一个 HTTP API，
项目通过 agent_sandbox SDK 与之通信。本配置描述镜像、端口、容器
生命周期管理方式以及各种输出截断上限。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SandboxLifecycle = Literal["auto", "manual"]


class SandboxConfig(BaseModel):
    """Docker 沙箱配置。"""

    use: str = Field(default="aio", description="沙箱提供方（仅支持 aio）")

    lifecycle: SandboxLifecycle = Field(
        default="auto", description="容器生命周期管理方式"
    )

    image: str = Field(
        default="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0",
        description="沙箱镜像（火山引擎公共仓库）",
    )

    port: int = Field(default=8080, description="沙箱 API 容器内端口")

    bind_host: str = Field(
        default="127.0.0.1", description="宿主端口绑定地址（WSL docker 用 0.0.0.0）"
    )

    container_prefix: str = Field(
        default="prism-sandbox", description="沙箱容器名前缀"
    )

    base_url: str | None = Field(
        default=None, description="已启动沙箱的 HTTP 地址（manual 模式必填）"
    )

    idle_timeout: int = Field(default=600, description="沙箱空闲销毁秒数(0=不销毁)")

    replicas: int = Field(default=1, ge=1, description="沙箱容器最大副本数")

    docker_command: list[str] = Field(
        default_factory=list, description="docker CLI 调用前缀"
    )

    mounts: list[dict[str, str]] = Field(
        default_factory=list, description="额外卷挂载列表"
    )

    environment: dict[str, str] = Field(
        default_factory=dict, description="注入容器的环境变量"
    )

    allow_host_bash: bool = Field(
        default=True, description="是否允许沙箱 shell 执行"
    )

    bash_command_timeout: int = Field(default=600, description="命令执行超时(秒)")

    bash_output_max_chars: int = Field(
        default=30000, description="命令输出最大字符数"
    )

    read_file_output_max_chars: int = Field(
        default=50000, description="读文件输出最大字符数"
    )

    ls_output_max_chars: int = Field(default=30000, description="列目录输出最大字符数")