"""
Rotating file and console logger for CarrotQuant.
Provides structured, timestamped logs with automatic rotation to prevent unbounded disk usage.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config.settings import settings


def setup_logger(
    name: str = "CarrotQuant",
    log_dir: Optional[Path] = None,
    log_level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB per log file
    backup_count: int = 5,
) -> logging.Logger:
    """
    Configures and returns a root application logger with both console and rotating file handlers.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Avoid duplicate handlers if already configured
    if logger.handlers:
        return logger

    # Log format: [2026-10-04 12:00:00,123] [INFO] [ModuleName] Log message
    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. Rotating File Handler
    target_dir = log_dir or settings.LOGS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / "trading.log"

    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
