"""
Database Package - Pure MongoDB Implementation
"""

from .mongodb import db, init_db, close_db

__all__ = ["db", "init_db", "close_db"]