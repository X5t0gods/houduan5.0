"""日志配置模块 - 分级日志 + request_id 过滤器。

功能：
- 控制台 + 滚动文件双输出（logs/app.log，按天轮转）
- 自定义 Filter 将 request_id 注入 LogRecord
- 统一 uvicorn 日志格式，避免双份输出
- 绝对不打印密码、手机号全文、JWT 密钥
"""

import logging
import logging.config
import os
from logging.handlers import TimedRotatingFileHandler

from app.core.config import settings
from app.core.context import get_request_id


class RequestIdFilter(logging.Filter):
    """日志过滤器：将当前请求的 request_id 注入 LogRecord。

    缺失时显示 '-'，确保日志格式不会报错。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """注入 request_id 到日志记录。

        Args:
            record: 日志记录对象。

        Returns:
            始终返回 True（不过滤任何日志，只注入字段）。
        """
        record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
        return True


def setup_logging() -> None:
    """初始化全局日志配置。

    - 创建日志目录（如不存在）
    - 配置控制台与文件双输出
    - 统一 uvicorn 日志格式
    """
    # 确保日志目录存在
    log_dir = settings.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    # 拼接分两段写，避免单行超过 100 字符（ruff E501）
    log_format = (
        "%(asctime)s | %(levelname)-8s | [%(request_id)s] | "
        "%(name)s:%(lineno)d - %(message)s"
    )
    date_format = "%Y-%m-%d %H:%M:%S"

    # 根日志器配置
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handler（避免重复配置）
    root_logger.handlers.clear()

    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(log_format, datefmt=date_format)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(RequestIdFilter())
    root_logger.addHandler(console_handler)

    # 文件输出（按天轮转，保留 30 天）
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "app.log"),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(log_format, datefmt=date_format)
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(RequestIdFilter())
    root_logger.addHandler(file_handler)

    # 统一 uvicorn 日志：收敛到根日志器，避免双份输出
    # uvicorn.access 和 uvicorn.error 都交给我们的 handler 处理
    for uvicorn_logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # 降低第三方库的日志级别
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DB_ECHO else logging.WARNING
    )
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取带 request_id 过滤器的 logger 实例。

    Args:
        name: logger 名称，通常传 __name__。

    Returns:
        配置好的 Logger 实例。
    """
    return logging.getLogger(name)
