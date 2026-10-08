from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

"""简易登录鉴权

    职责：app 层的身份网关——用户表读写 + 无状态 HMAC token 签发与校验
        - 用户表 users.json：登录名 → (密码哈希, user_id)，双通道同一身份
        - token 格式 user_id.exp.sig，7 天有效，服务端零状态
        - 密钥优先级：环境变量 > 落盘文件 > 现场随机（仅测试）

    对外暴露：
        - DEFAULT_CREDENTIALS / TOKEN_TTL_SECONDS
        - authenticate / issue_token / verify_token
"""

# ── 常量：一份表意清楚，别处不重复定义 ──────────────────────────────────────

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # token 有效期：7 天
_USER_ID_RE = r"^[A-Za-z0-9_-]{1,64}$"

# 默认凭据表：(登录名, 明文密码) → user_id。密码在写入 users.json 时哈希。
DEFAULT_CREDENTIALS: list[dict[str, str]] = [
    {"username": "admin", "password": "admin", "user_id": "huihui"},
    {"username": "huihui", "password": "123456", "user_id": "huihui"},
]

_ENV_SECRET_KEY = "PRISM_AUTH_SECRET"  # 显式指定签名密钥（可选）
_write_lock = threading.Lock()  # 用户表/密钥文件写入互斥（本地单实例够用）


# ── 路径解析：全部锚定 harness paths 的 base_dir（.prism-worker/）──────────


def _users_file() -> Path:
    """用户表文件路径：{base_dir}/users.json。"""
    from harness.config.paths import get_paths

    # 锚定 harness paths 的 base_dir，避免各处自己拼路径
    return get_paths().base_dir / "users.json"


def _secret_file() -> Path:
    """签名密钥文件路径：{base_dir}/data/auth_secret.key。"""
    from harness.config.paths import get_paths

    # 与用户表同盘：密钥持久化后重启仍能验签
    return get_paths().base_dir / "data" / "auth_secret.key"


def _hash_password(plain: str) -> str:
    """明文密码 → sha256 十六进制（演示级混淆，见模块头注释）。"""
    # 演示级混淆：只防明文落盘，不是安全级哈希（见模块头）
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


# ── 1.用户表：加载 / seed / 验密 ─────────────────────────────────────────────


def _seed_users() -> dict[str, dict[str, str]]:
    """首次运行：按 DEFAULT_CREDENTIALS 生成用户表并落盘。

    表结构：{ username: {"password_hash": ..., "user_id": ...} }
    （以登录名为 key：一个 user_id 可挂多个登录名，即「双通道同一身份」。）
    """
    table: dict[str, dict[str, str]] = {}
    # 以登录名为 key：一个 user_id 可挂多个登录名（双通道同一身份）
    for cred in DEFAULT_CREDENTIALS:
        table[cred["username"]] = {
            "password_hash": _hash_password(cred["password"]),
            "user_id": cred["user_id"],
        }
    # 首次运行落盘：父目录先建，再整体写入
    path = _users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2), "utf-8")
    return table


def _load_users() -> dict[str, dict[str, str]]:
    """读用户表；文件不存在则 seed。坏文件直接抛错（宁可启动可见地失败）。"""
    path = _users_file()
    # 1.文件不存在则 seed（双检：并发首启只有一个成功）
    if not path.exists():
        with _write_lock:
            if not path.exists():  # 双检：并发首启只有一个 seed 成功
                return _seed_users()
    data: Any = json.loads(path.read_text("utf-8"))
    # 2.结构非法直接抛：宁可启动时可见地失败，也不要静默半可用
    if not isinstance(data, dict):
        raise ValueError("users.json 结构非法：期望 {username: {...}} 对象")
    return data


def authenticate(username: str, password: str) -> str | None:
    """校验用户名+密码，成功返回 user_id，失败返回 None（不区分「用户不存在/密码错」）。"""
    # 1.用户名先 trim；查不到直接判失败
    entry = _load_users().get((username or "").strip())
    if not entry:
        return None
    # 2.常量时间比较哈希，防时序侧信道
    if not hmac.compare_digest(entry.get("password_hash", ""), _hash_password(password or "")):
        return None
    # 3.不区分「用户不存在 / 密码错」，避免被枚举用户名
    return entry["user_id"]


# ── 2.签名密钥：env 优先，否则文件持久化（随机生成一次）────────────────────


def _get_secret() -> bytes:
    """取 HMAC 签名密钥。

    优先级：
      1. 环境变量 PRISM_AUTH_SECRET（部署时显式指定）；
      2. {base_dir}/data/auth_secret.key（首次随机生成并落盘 → 重启不掉线）；
      3. 都没有时现场生成随机值（不落盘，仅供测试；重启即失效）。
    """
    # 1.环境变量优先（部署时显式指定）
    env = os.getenv(_ENV_SECRET_KEY, "").strip()
    if env:
        return env.encode("utf-8")
    # 2.落盘文件次之：重启后旧 token 仍有效
    path = _secret_file()
    if path.exists():
        return path.read_bytes()
    with _write_lock:
        # 3.双检防并发重复生成；生成即落盘
        if not path.exists():  # 双检防并发重复生成
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


# ── 3.token：签发 / 校验（无状态，格式 user_id.exp.sig）─────────────────────


def _sign(payload: str) -> str:
    """对 payload 做 HMAC-SHA256，取前 32 位十六进制（够短够安全，本地应用）。"""
    # 取前 32 位十六进制：本地应用够短也够安全
    return hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def issue_token(user_id: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    """签发 token：payload(user_id.exp) + 签名，拼成不透明字符串。"""
    exp = int(time.time()) + ttl_seconds
    # payload = user_id.exp；再拼签名，整体是不透明字符串
    payload = f"{user_id}.{exp}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """校验 token，通过返回 user_id，否则 None（缺失/篡改/过期统一按 None 处理）。"""
    # 从右切两段：sig 与 exp 不含点，user_id 允许含点
    parts = (token or "").rsplit(".", 2)  # 从右切两段：sig 与 exp 不含点，user_id 允许含点
    if len(parts) != 3:
        return None
    user_id, exp_str, sig = parts
    payload = f"{user_id}.{exp_str}"
    # 1.验签（常量时间比较，防时序侧信道）
    # 1.先验签（常量时间比较，防时序侧信道）
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    # 2.验过期
    # 2.再验过期（时间戳非数字按无效处理）
    try:
        if int(exp_str) < time.time():
            return None
    except ValueError:
        return None
    # 3.兜底：user_id 字符合法性（与 harness paths 的校验口径一致）
    # 3.兜底：user_id 字符合法性（与 harness paths 校验口径一致）
    if not re.fullmatch(_USER_ID_RE, user_id):
        return None
    return user_id
