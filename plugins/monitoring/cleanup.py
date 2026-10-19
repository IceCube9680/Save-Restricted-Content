# plugins/monitoring/cleanup.py
import os
import asyncio
import time
import shutil
from datetime import datetime, timedelta
from typing import Dict, List

from pyrogram import Client

from config import (
    CLEANUP_INTERVAL, FILE_RETENTION_TIME, MAX_DOWNLOAD_DIR_SIZE,
    BACKUP_ENABLED, BACKUP_INTERVAL, BACKUP_DIR, MAX_BACKUP_COUNT
)
from database.mongodb import db  # Changed from database.db
from plugins.core.utils import (
    get_logger, get_downloads_dir, get_temp_dir, get_thumbnails_dir,
    safe_delete_file, humanbytes, ensure_directory
)
from plugins.services.session_manager import session_manager
from plugins.services.downloader import download_service
from plugins.monitoring.metrics import usage_stats

logger = get_logger(__name__)


async def start_cleanup_scheduler(bot: Client):
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            logger.info("🧹 Running periodic cleanup...")

            cleaned_files = await cleanup_old_files()
            cleaned_logs = await db.cleanup_old_backup_logs(30)

            logger.info(f"🧹 Cleanup complete. Cleaned {cleaned_files} files, {cleaned_logs} backup logs.")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup scheduler: {e}", exc_info=True)


async def cleanup_old_files(hours: int = None) -> int:
    """Clean up old download files"""
    if hours is None:
        hours = FILE_RETENTION_TIME / 3600
    
    cleaned = 0
    directories = [
        get_downloads_dir(),
        get_temp_dir(),
        get_thumbnails_dir()
    ]
    
    cutoff_time = time.time() - (hours * 3600)
    
    for directory in directories:
        if os.path.exists(directory):
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.isfile(file_path):
                    try:
                        # Check file age
                        file_mtime = os.path.getmtime(file_path)
                        if file_mtime < cutoff_time:
                            await safe_delete_file(file_path)
                            cleaned += 1
                    except Exception as e:
                        logger.error(f"Error deleting {file_path}: {e}")
    
    return cleaned


def get_disk_usage() -> str:
    """Get disk usage percentage"""
    try:
        usage = shutil.disk_usage(".")
        percent = (usage.used / usage.total) * 100
        return f"{percent:.1f}%"
    except:
        return "N/A"