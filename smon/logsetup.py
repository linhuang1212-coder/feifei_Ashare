"""日志配置 —— loguru 统一管理。每道工序 get_logger(step) 后打印,出错秒定位。

    from smon.logsetup import setup_logger, get_logger
    setup_logger("INFO", "logs/feifei.log")
    log = get_logger("fetch"); log.info("取数 600519 ...")
"""
import sys
from pathlib import Path

from loguru import logger

_CONFIGURED = False

_CONSOLE_FMT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
    "<cyan>{extra[step]: <8}</cyan> | <level>{message}</level>"
)
_FILE_FMT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {extra[step]: <8} | {message}"


def setup_logger(level: str = "INFO", logfile: str | None = None):
    """配置全局 logger(幂等,可重复调用)。"""
    global _CONFIGURED
    logger.remove()
    logger.configure(extra={"step": "-"})
    logger.add(sys.stderr, level=level.upper(), format=_CONSOLE_FMT, colorize=True, enqueue=False)
    if logfile:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        logger.add(logfile, level="DEBUG", rotation="10 MB", retention=5,
                   encoding="utf-8", format=_FILE_FMT, enqueue=True)
    _CONFIGURED = True
    return logger


def get_logger(step: str = "-"):
    if not _CONFIGURED:
        setup_logger()
    return logger.bind(step=step)
