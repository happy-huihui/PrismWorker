from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_app_config
from app.api.schemas import ModelOut

"""模型清单路由

    职责：返回可用模型清单（只暴露非敏感字段，api_key 不外传）

    对外暴露：
        - router
"""

router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[ModelOut], summary="模型清单")
async def list_models(config: Any = Depends(get_app_config)) -> list[ModelOut]:
    """返回可用的模型清单（只暴露非敏感字段，api_key 不外传）。"""
    # 只暴露非敏感字段：api_key 等配置项不外传
    return [
        ModelOut(
            name=m.name,
            provider=m.provider,
            model=m.model,
            supports_vision=m.supports_vision,
            supports_thinking=m.supports_thinking,
        )
        for m in (config.models or [])
    ]
