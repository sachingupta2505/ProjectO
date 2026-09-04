"""
Logger configuration using Rich and rotating file logging.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from rich.console import Console
from rich.logging import RichHandler
from config.settings import settings

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_logger(name: str = "TradingBot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # Console handler with Rich formatting
    console = Console(force_terminal=True, legacy_windows=False)
    rich_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        tracebacks_show_locals=False,
    )
    rich_handler.setLevel(logging.INFO)
    rich_formatter = logging.Formatter("%(message)s")
    rich_handler.setFormatter(rich_formatter)
    logger.addHandler(rich_handler)

    # File handler with rotation
    log_file = settings.LOG_DIR / "trading_bot.log"
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    return logger
