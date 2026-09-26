from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

"""沙箱配置（Docker all-in-one-sandbox）

    职责：描述沙箱镜像、端口、容器生命周期，以及各类输出的截断上限。
    背景：沙箱跑在本机 Docker 里，镜像 all-in-one-sandbox 暴露一个 HTTP API，
         项目通过 agent_sandbox SDK 与之通信。

    对外暴露：
        - SandboxLifecycle  auto=按需起停 / manual=外部已起好
        - SandboxMount      一条卷挂载（宿主目录 → 容器路径）
        - SandboxConfig     沙箱总配置
"""

SandboxLifecycle = Literal["auto", "manual"]


class SandboxMount(BaseModel):
    """一条卷挂载配置（宿主目录 → 容器路径）。

    字段命名沿用本仓库既有的 source / target；同时兼容参考实现
    （DeerFlow 的 host_path / container_path）与旧配置里出现过的
    src / dst / dest 写法，避免升级时静默失效。
    """

    source: str = Field(..., description="宿主侧源目录（相对路径以项目根为基准）")
    target: str = Field(..., description="容器内目标路径（必须是绝对路径）")
    read_only: bool = Field(
        default=False,
        description="是否只读挂载。技能目录必须为 True —— 否则模型能改写技能包",
    )

    @model_validator(mode="before")
    @classmethod
    def _accept_aliases(cls, data: object) -> object:
        """兼容 source/src/host_path 与 target/dst/dest/container_path 别名。"""
        # 非字典直接放行
        if not isinstance(data, dict):
            return data
        aliased: dict[str, object] = dict(data)
        # 逐个规范字段：已写规范名的跳过，否则找别名顶上
        for canonical, names in (
            ("source", ("src", "host_path")),
            ("target", ("dst", "dest", "container_path")),
            ("read_only", ("readonly", "ro")),
        ):
            if canonical in aliased:
                continue
            for name in names:
                if name in aliased:
                    aliased[canonical] = aliased.pop(name)
                    break
        return aliased


class SandboxConfig(BaseModel):
    """Docker 沙箱配置。"""

    # ── 提供方与镜像 ──────────────────────────────────────────────
    use: str = Field(default="aio", description="沙箱提供方（仅支持 aio）")

    image: str = Field(
        default="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0",
        description="沙箱镜像（火山引擎公共仓库）",
    )

    # ── 容器生命周期 ──────────────────────────────────────────────
    lifecycle: SandboxLifecycle = Field(
        default="auto", description="容器生命周期管理方式"
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

    # ── 卷挂载 ────────────────────────────────────────────────────
    mounts: list[SandboxMount] = Field(
        default_factory=list,
        description="额外卷挂载列表（技能目录由 mount_skills 自动挂载，不必写在这里）",
    )

    mount_skills: bool = Field(
        default=True,
        description=(
            "是否把技能目录以**只读**方式挂载到容器 container_path。"
            "技能目录是全线程共享的同一份宿主目录（bind mount，不复制数据），"
            "因此不参与 per-thread 隔离，挂载开销可忽略。"
        ),
    )

    environment: dict[str, str] = Field(
        default_factory=dict, description="注入容器的环境变量"
    )

    # ── 执行与输出截断 ────────────────────────────────────────────
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
