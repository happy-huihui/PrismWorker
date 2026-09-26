"""火山方舟豆包生图接入（ark_image）。

把方舟（Ark）的 doubao-seedream 系列生图能力接成 Lead Agent 的一个工具。

模块划分：
    - client.py  ArkImageClient：封装 `/images/generations` 同步 HTTP 接口
    - tools.py   generate_image 工具：调接口 → 落盘 outputs → 登记产物
    - config     （在 harness.config.tool_config.ArkImageConfig）

对外暴露：
    - ArkImageClient    生图客户端
    - ArkImageResult    单次生图结果
    - ArkImageError     生图失败异常
    - build_client      按全局配置构造客户端
    - generate_image_tool  给 Agent 用的 generate_image 工具
"""

from __future__ import annotations

from harness.community.ark_image.client import (
    ArkImageClient,
    ArkImageError,
    ArkImageResult,
    build_client,
)
from harness.community.ark_image.tools import generate_image_tool

__all__ = [
    "ArkImageClient",
    "ArkImageError",
    "ArkImageResult",
    "build_client",
    "generate_image_tool",
]
