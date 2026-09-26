"""API 请求/响应模型（schemas）——Pydantic v2 标准模型。

为前端提供稳定契约，同时承担输入校验（字段类型/必填/长度）。模型直接映射
core 层数据结构（ThreadMeta / RunRecord / RunHandle 的公开字段），不引入
多余概念。字段命名与 core 一致，前端直接消费。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

"""
    API 契约速览：
        POST   /threads                     → ThreadOut
        GET    /threads                     → list[ThreadOut]
        GET    /threads/{thread_id}         → ThreadOut
        PATCH  /threads/{thread_id}         → ThreadOut（改名）
        DELETE /threads/{thread_id}         → 204
        POST   /threads/{thread_id}/runs    → RunOut
        GET    /threads/{thread_id}/runs    → list[RunOut]
        GET    /runs/{run_id}               → RunOut
        POST   /runs/{run_id}/cancel        → RunOut
        GET    /runs/{run_id}/stream        → SSE（非 JSON）
        GET    /models                      → list[ModelOut]
        GET    /threads/{thread_id}/messages → list[MessageOut]
        POST   /threads/{thread_id}/uploads → UploadOut
        GET    /threads/{thread_id}/uploads → list[UploadOut]
        GET    /threads/{thread_id}/artifacts/{path:path} → 文件流
"""



class ThreadCreate(BaseModel):
    """创建线程的请求体。"""

    title: str = Field(default="新会话", max_length=200, description="线程标题")


class ThreadRename(BaseModel):
    """重命名线程的请求体。"""

    title: str = Field(min_length=1, max_length=200, description="新标题")


class ThreadOut(BaseModel):
    """线程响应模型（映射 ThreadMeta）。"""

    thread_id: str = Field(description="线程 id")
    user_id: str = Field(description="归属用户 id")
    title: str = Field(description="线程标题")
    created_at: float = Field(description="创建时间戳")
    updated_at: float = Field(description="最后更新时间戳")
    message_count: int = Field(description="累计消息数")
    last_message_preview: str = Field(description="最后消息预览")
    status: str = Field(description="线程状态 (idle/running)")



class RunCreate(BaseModel):
    """创建 run 的请求体。"""

    messages: list[dict[str, Any]] = Field(
        min_length=1,
        description="对话消息列表（[{type,content}, ...] 或带 role 的 dict）",
    )
    model_name: str | None = Field(default=None, description="模型名（缺省用配置默认）")
    thinking_enabled: bool = Field(default=False, description="是否启用深度思考")


class RunOut(BaseModel):
    """run 响应模型（映射 RunRecord 公开字段）。"""

    run_id: str = Field(description="run id")
    thread_id: str = Field(description="归属线程 id")
    user_id: str = Field(description="归属用户 id")
    status: str = Field(description="run 状态")
    model_name: str = Field(description="使用的模型名")
    input_preview: str = Field(description="输入预览")
    error: str | None = Field(default=None, description="错误摘要（error 状态）")
    artifacts: list[str] = Field(default_factory=list, description="交付物虚拟路径")
    message_count: int = Field(default=0, description="结束时消息总数")
    created_at: float = Field(description="创建时间戳")
    started_at: float | None = Field(default=None, description="开始执行时间戳")
    finished_at: float | None = Field(default=None, description="结束时间戳")



class ChainOut(BaseModel):
    """一个历史 run 的思考链回放包（事件流 + 计时元信息）。"""

    run_id: str = Field(description="归属 run")
    status: str = Field(description="run 终态")
    model_name: str = Field(default="", description="使用的模型名")
    started_at: float | None = Field(default=None, description="开始时间戳")
    finished_at: float | None = Field(default=None, description="结束时间戳")
    events: list[dict[str, Any]] = Field(
        default_factory=list, description="紧凑事件流 [{event, data}, ...]，前端用同一 reducer 回放"
    )


class ModelOut(BaseModel):
    """模型清单响应（只暴露非敏感字段）。"""

    name: str = Field(description="模型唯一名称")
    provider: str = Field(description="提供方（openai / deepseek / mimo）")
    model: str = Field(description="模型标识")
    supports_vision: bool = Field(description="是否支持视觉输入")
    supports_thinking: bool = Field(description="是否支持思考模式")



class MessageOut(BaseModel):
    """单条会话消息（精简结构，前端可直接渲染）。"""

    role: str = Field(description="角色（user/assistant/tool/system/message）")
    content: str = Field(default="", description="消息文本内容")



class UploadOut(BaseModel):
    """上传文件信息（落盘 + 虚拟路径）。"""

    filename: str = Field(description="文件名")
    size: int = Field(description="文件字节数")
    virtual_path: str = Field(description="沙箱虚拟路径（/mnt/user-data/uploads/...）")



def thread_out_from_meta(meta: Any) -> ThreadOut:
    """从 ThreadMeta 构造 ThreadOut。"""
    return ThreadOut(
        thread_id=meta.thread_id,
        user_id=meta.user_id,
        title=meta.title,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        message_count=meta.message_count,
        last_message_preview=meta.last_message_preview,
        status=meta.status,
    )


def run_out_from_record(record: Any) -> RunOut:
    """从 RunRecord 构造 RunOut。"""
    return RunOut(
        run_id=record.run_id,
        thread_id=record.thread_id,
        user_id=record.user_id,
        status=record.status,
        model_name=record.model_name,
        input_preview=record.input_preview,
        error=record.error,
        artifacts=list(record.artifacts),
        message_count=record.message_count,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )