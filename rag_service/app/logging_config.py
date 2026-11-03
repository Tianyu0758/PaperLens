# 文件说明：初始化 RAG 日志输出，统一日志格式和文件存储位置。
# 供服务启动及业务模块记录入库、检索、删除和异常信息，便于定位链路问题。

import logging
from logging.handlers import TimedRotatingFileHandler

from app.config import Settings


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("rag")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(module)s | %(message)s"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        settings.log_path / "app.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger
