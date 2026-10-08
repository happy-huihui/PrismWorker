from __future__ import annotations

from harness.community.ark_image.client import (
    ArkImageClient,
    ArkImageError,
    ArkImageResult,
    build_client,
)
from harness.community.ark_image.tools import generate_image_tool

"""火山方舟生图接入

    职责：把方舟 doubao-seedream 系列生图能力接成 Lead Agent 的一个工具
        - client：封装 /images/generations 同步 HTTP 接口
        - tools：调接口 → 落盘 outputs → 登记产物

    对外暴露：
        - ArkImageClient / ArkImageResult / ArkImageError
        - build_client
        - generate_image_tool
"""

__all__ = [
    "ArkImageClient",
    "ArkImageError",
    "ArkImageResult",
    "build_client",
    "generate_image_tool",
]
