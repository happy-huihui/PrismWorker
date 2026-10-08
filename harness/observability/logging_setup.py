from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from harness.observability import context, db_sink

"""结构化日志装配（logging_setup）——把 stdlib logging 升级为 structlog 结构化输出。

    职责：给 Python 根日志换一个"处理器"，建立 3 个输出：
            - 控制台：彩色 pretty（人看）
            - 文件：JSON Lines（机器看）
            - 落库：入队到 db_sink（进 logs 表）
          任何一条 logger.info("xxx") 都会自动带上 trace_id/run_id/span_id，业务代码无感知。

    对外暴露：
        - setup_observability_logging   配置根日志（替换 app.runner 的 _setup_logging）
        - get_observability_logger      取 structlog 结构化 logger（新代码埋点用）
"""

# 文件 JSON 日志名（机器读的结构化版本）
_JSON_LOG_NAME = "app.jsonl"


def _inject_context(_logger: Any, _method: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    """把观测上下文注入 event_dict（processor；字段缺失不注入，保持日志干净）。

    参数：
        _logger / _method: structlog 处理器约定签名，此处不用
        event_dict: 待输出的日志事件

    返回：
        追加了 trace/run/span 等字段的同一字典
    """
    # 1.一次性取上下文快照，避免反复跨 contextvar 取值
    fields = context.snapshot()
    # 2.只有非空字段才写入，避免每条日志都带一堆空串占位
    for key, value in fields.items():
        if value:
            event_dict[key] = value
    return event_dict


def _build_formatters() -> tuple[Any, Any]:
    """构造「控制台 / 文件」两个 ProcessorFormatter（共享前置处理链）。

    返回：
        (console_formatter, json_formatter)
    """
    # 1.前置链：把 stdlib LogRecord 规整成 event_dict（logger/level/时间/观测上下文）
    pre_chain = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        _inject_context,
    ]
    # 2.控制台：彩色 pretty，给人看
    console = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=True),
        ],
    )
    # 3.文件：JSON Lines，给机器/中台解析
    json_fmt = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    return console, json_fmt


class _DBHandler(logging.Handler):
    """落库 handler：只触发 formatter 的入队副作用，不真正打印。"""

    def emit(self, record: logging.LogRecord) -> None:
        """格式化（触发 enqueue 副作用），丢弃渲染结果。"""
        # format() 会跑一遍 processor 链，db_sink_processor 在其中有入队副作用
        self.format(record)


def _empty_renderer(_logger: Any, _method: Any, _event_dict: dict[str, Any]) -> str:
    """DB formatter 的末端渲染器：返回空串（handler 无输出）。"""
    return ""


def _build_db_formatter() -> Any:
    """构造 DB handler 的 ProcessorFormatter（副作用入队，无输出）。

    返回：
        落库用的 formatter
    """
    # 1.与文件 JSON 同款前置链：logger/level/时间/观测上下文
    pre_chain = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        _inject_context,
    ]
    # 2.主链：剥 meta → 入队（副作用）→ 空渲染器
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            db_sink.db_sink_processor,
            _empty_renderer,
        ],
    )


def _configure_native_structlog() -> None:
    """配置 structlog 原生入口，使新代码 `structlog.get_logger()` 与根日志口径一致。"""
    # 与根日志同款处理器链：新埋点代码也能产出同样的结构化字段
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            _inject_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def setup_observability_logging(log_dir: str | Path, *, level: str = "INFO") -> Path:
    """配置根日志：控制台 pretty + 文件 JSON 双输出。返回 JSON 日志文件路径。

    参数：
        log_dir: 日志目录
        level: 日志级别（DEBUG / INFO / WARNING / ERROR）

    返回：
        结构化 JSON 日志文件的绝对路径
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    json_file = log_path / _JSON_LOG_NAME

    # 1.让新代码的 structlog.get_logger() 与根日志口径一致
    _configure_native_structlog()

    # 2.装配两个 formatter（控制台 / 文件）
    console_formatter, json_formatter = _build_formatters()

    # 3.重置根日志：清掉旧 handler，重新挂控制台 + 文件
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(console_formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        str(json_file), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(json_formatter)
    root.addHandler(file_handler)

    # 4.落库 handler：把 INFO+ 结构化日志异步写进 logs 表（与文件双写）
    db_handler = _DBHandler()
    db_handler.setLevel(logging.INFO)
    db_handler.setFormatter(_build_db_formatter())
    root.addHandler(db_handler)
    db_sink.start_db_sink()

    # 5.第三方噪声库统一压到 WARNING，避免刷屏
    for noisy in ("uvicorn.access", "httpcore", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return json_file


def get_observability_logger(name: str | None = None) -> Any:
    """取一个 structlog 结构化 logger（新埋点代码用，event 传动词短语）。

    参数：
        name: logger 名；None 用 "prism.observability"

    返回：
        structlog BoundLogger
    """
    return structlog.get_logger(name or "prism.observability")
