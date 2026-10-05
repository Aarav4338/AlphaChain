"""
Storage and logging package for CarrotQuant.
Includes embedded SQLite database manager and rotating logger.
"""
from storage.db import Database
from storage.logger import setup_logger

__all__ = ["Database", "setup_logger"]
