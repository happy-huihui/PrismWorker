"""PrismWorker 后端启动入口（runner）—— `python -m app.runner`。

阶段16 部署硬化：把「手动敲 uvicorn」变成可配置、可校验、有日志落盘的
正式启动入口。

功能：
  1. 可选加载项目根 `.env`（KEY=VALUE 手写解析，不覆盖已有环境变量）
  2. 启动前配置校验：配置缺失 / 模型为空 / active_model 指向不存在等
     问题给出中文指引；致命项直接退出（exit 1）
  3. 统一日志：控制台 + 滚动文件 `{log_dir}/app.log`（默认 5MB × 3 份）
  4. 委托 uvicorn 启动 `app.api.main:app`（lifespan 负责启动初始化与关闭
     回收——含真实沙箱容器 stop、在跑 run 取消）

用法：
  python -m app.runner                                  # 0.0.0.0:8000
  python -m app.runner --host 127.0.0.1 --port 8080
  python -m app.runner --reload                         # 仅开发
  python -m app.runner --config ./config.yaml --log-dir ./logs
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

_DEFAULT_BASE_HINT = os.getenv("PRISM_WORKER_HOME") or str(Path.cwd() / ".prism-worker")
_DEFAULT_LOG_DIR = os.getenv("PRISM_LOG_DIR") or str(Path(_DEFAULT_BASE_HINT) / "logs")


def _load_dotenv(path: str | Path | None = None) -> None:
    """加载项目根 .env 文件（KEY=VALUE），不覆盖进程已有环境变量。

    path 缺省 = 项目根 `.env`（与 config.yaml 同级）。文件不存在静默跳过。
    支持 # 注释与空行；`KEY=value` 与 `KEY="value"` 两种写法。
    """
    env_file = Path(path) if path else Path.cwd() / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _setup_logging(log_dir: str | Path, level: str) -> Path:
    """配置根日志：控制台 + 滚动文件。返回日志文件路径。"""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "app.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        str(log_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    for noisy in ("uvicorn.access", "httpcore", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def _validate_and_report(config_path: str | None) -> bool:
    """启动前配置校验（返回 True=可启动 / False=致命错误应退出）。"""
    from app.core.config import validate_app_config

    if config_path:
        from harness.config.app_config import AppConfig

        try:
            cfg = AppConfig.from_file(config_path)
            from app.core.config import set_app_config

            set_app_config(cfg)
        except Exception as exc:  # noqa: BLE001 —— 配置解析失败给中文指引
            print(f"[配置错误] 无法加载配置文件 {config_path}: {exc}")
            print("  请检查 YAML 语法与环境变量（${VAR} 引用）。")
            return False

    problems = validate_app_config()
    fatal_patterns = ("models 为空", "active_model=", "无法加载")
    fatal = [p for p in problems if any(p.startswith(k) for k in fatal_patterns)]
    warning = [p for p in problems if p not in fatal]

    if fatal:
        print("[配置错误] 以下问题导致服务无法启动：")
        for item in fatal:
            print(f"  - {item}")
        print("  修复指引：复制 config.example.yaml 为 config.yaml 后按需修改。")
        return False
    if warning:
        print("[配置提示] （不影响启动，但请注意）：")
        for item in warning:
            print(f"  - {item}")
    return True


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器（独立函数便于测试）。"""
    parser = argparse.ArgumentParser(
        prog="python -m app.runner",
        description="PrismWorker 后端启动入口",
    )
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    parser.add_argument(
        "--config", default=None, help="配置文件路径（默认走加载链 ./config.yaml）"
    )
    parser.add_argument(
        "--log-dir", default=_DEFAULT_LOG_DIR, help=f"日志目录（默认 {_DEFAULT_LOG_DIR}）"
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("PRISM_LOG_LEVEL", "INFO"),
        help="日志级别：DEBUG/INFO/WARNING/ERROR（默认 INFO）",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """启动入口（返回进程退出码）。"""
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    _load_dotenv()

    if not _validate_and_report(args.config):
        return 1

    if not args.reload:
        log_file = _setup_logging(args.log_dir, args.log_level)
        print(f"日志文件: {log_file}")

    import uvicorn

    print(f"启动 PrismWorker API: http://{args.host}:{args.port}")
    print("健康检查: GET /health （开发期未配置 PRISM_INTERNAL_TOKEN 即可访问）")
    uvicorn.run(
        "app.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())