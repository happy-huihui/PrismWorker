"""简易登录鉴权（auth）——app 层的「身份网关」能力模块。

职责边界（只做两件事，路由/存储都不管）：
  1. 用户表：读写 {base_dir}/users.json，校验「用户名 + 密码」→ 换出 user_id；
  2. 令牌：签发/校验无状态 HMAC token（格式 user_id.exp.sig），7 天有效。

设计取舍（简易版，刻意不做的东西）：
  - 不建会话表 / 不做 refresh token：token 自含过期时间戳，验签即通过，
    服务端零状态，重启不掉线（密钥持久化在文件里）；
  - 密码只存 sha256：这是「演示级」保护（防明文落盘），不是安全级——
    生产环境应换 bcrypt/argon2，此处刻意不引入额外依赖；
  - 不做注册接口：用户表由代码 seed 两条默认凭据（见 DEFAULT_CREDENTIALS）。

默认凭据（两条通道，同一个身份 huihui）：
  - admin  / admin   ←「管理员口」，登录名 admin，密码 admin
  - huihui / 123456  ←「本人口」，直接用显示名登录
  两者验密通过后都映射到 user_id="huihui"，即共用同一份会话/记忆数据。

依赖方向：app → harness（只用 paths 拿 base_dir），符合分层铁律；
harness 不知道本模块存在。
"""

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

    return get_paths().base_dir / "users.json"


def _secret_file() -> Path:
    """签名密钥文件路径：{base_dir}/data/auth_secret.key。"""
    from harness.config.paths import get_paths

    return get_paths().base_dir / "data" / "auth_secret.key"


def _hash_password(plain: str) -> str:
    """明文密码 → sha256 十六进制（演示级混淆，见模块头注释）。"""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


# ── 1.用户表：加载 / seed / 验密 ─────────────────────────────────────────────


def _seed_users() -> dict[str, dict[str, str]]:
    """首次运行：按 DEFAULT_CREDENTIALS 生成用户表并落盘。

    表结构：{ username: {"password_hash": ..., "user_id": ...} }
    （以登录名为 key：一个 user_id 可挂多个登录名，即「双通道同一身份」。）
    """
    table: dict[str, dict[str, str]] = {}
    for cred in DEFAULT_CREDENTIALS:
        table[cred["username"]] = {
            "password_hash": _hash_password(cred["password"]),
            "user_id": cred["user_id"],
        }
    path = _users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2), "utf-8")
    return table


def _load_users() -> dict[str, dict[str, str]]:
    """读用户表；文件不存在则 seed。坏文件直接抛错（宁可启动可见地失败）。"""
    path = _users_file()
    if not path.exists():
        with _write_lock:
            if not path.exists():  # 双检：并发首启只有一个 seed 成功
                return _seed_users()
    data: Any = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("users.json 结构非法：期望 {username: {...}} 对象")
    return data


def authenticate(username: str, password: str) -> str | None:
    """校验用户名+密码，成功返回 user_id，失败返回 None（不区分「用户不存在/密码错」）。"""
    entry = _load_users().get((username or "").strip())
    if not entry:
        return None
    if not hmac.compare_digest(entry.get("password_hash", ""), _hash_password(password or "")):
        return None
    return entry["user_id"]


# ── 2.签名密钥：env 优先，否则文件持久化（随机生成一次）────────────────────


def _get_secret() -> bytes:
    """取 HMAC 签名密钥。

    优先级：
      1. 环境变量 PRISM_AUTH_SECRET（部署时显式指定）；
      2. {base_dir}/data/auth_secret.key（首次随机生成并落盘 → 重启不掉线）；
      3. 都没有时现场生成随机值（不落盘，仅供测试；重启即失效）。
    """
    env = os.getenv(_ENV_SECRET_KEY, "").strip()
    if env:
        return env.encode("utf-8")
    path = _secret_file()
    if path.exists():
        return path.read_bytes()
    with _write_lock:
        if not path.exists():  # 双检防并发重复生成
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


# ── 3.token：签发 / 校验（无状态，格式 user_id.exp.sig）─────────────────────


def _sign(payload: str) -> str:
    """对 payload 做 HMAC-SHA256，取前 32 位十六进制（够短够安全，本地应用）。"""
    return hmac.new(_get_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def issue_token(user_id: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    """签发 token：payload(user_id.exp) + 签名，拼成不透明字符串。"""
    exp = int(time.time()) + ttl_seconds
    payload = f"{user_id}.{exp}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> str | None:
    """校验 token，通过返回 user_id，否则 None（缺失/篡改/过期统一按 None 处理）。"""
    parts = (token or "").rsplit(".", 2)  # 从右切两段：sig 与 exp 不含点，user_id 允许含点
    if len(parts) != 3:
        return None
    user_id, exp_str, sig = parts
    payload = f"{user_id}.{exp_str}"
    # 1.验签（常量时间比较，防时序侧信道）
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    # 2.验过期
    try:
        if int(exp_str) < time.time():
            return None
    except ValueError:
        return None
    # 3.兜底：user_id 字符合法性（与 harness paths 的校验口径一致）
    if not re.fullmatch(_USER_ID_RE, user_id):
        return None
    return user_id
