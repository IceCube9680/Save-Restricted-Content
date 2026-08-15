"""
Download Service
"""

import os
import time
import asyncio
from typing import Optional, Tuple
from datetime import datetime

from pyrogram import Client
from pyrogram.errors import FloodWait, FileIdInvalid, FileReferenceEmpty
from pyrogram.types import Message

from config import DOWNLOAD_TIMEOUT, MAX_FILE_SIZE, ERROR_MESSAGE, UPDATE_INTERVAL
from database.mongodb import db
from plugins.core.models import FileTask, TaskStatus, BatchCancel, DownloadResult
from plugins.core.utils import (
    get_logger, get_downloads_dir, safe_delete_file,
    sanitize_filename, validate_file_size, retry_on_error
)
from plugins.core.constants import FileType
from plugins.monitoring.metrics import usage_stats
from plugins.services.queue_manager import queue_manager
from plugins.progress_display import progress_callback

logger = get_logger(__name__)


class DownloadService:
    """Service for downloading files from Telegram"""
    
    def __init__(self):
        self.downloads_dir = get_downloads_dir()
        self.active_downloads: Dict[str, asyncio.Task] = {}
    
    async def download_media(
        self,
        client: Client,
        message: Message,
        msg: Message,
        task: Optional[FileTask] = None,
        user_id: Optional[int] = None
    ) -> DownloadResult:
        """Download media with progress tracking"""
        download_id = f"{task.from_user_id}_{task.msgid}" if task else f"temp_{time.time()}"
        start_time = time.time()
        
        try:
            # Check if download is cancelled
            if task:
                if task.status == TaskStatus.CANCELLED:
                    raise BatchCancel("Download cancelled by user")
                elif task.status.value == "skipped":
                    raise Exception("Task skipped by user")
            
            # Generate filename
            file_name = await self._generate_filename(msg, task)
            file_path = os.path.join(self.downloads_dir, sanitize_filename(file_name))
            
            # Check file size
            file_size = self._get_file_size(msg)
            if not validate_file_size(file_size):
                logger.warning(f"File too large: {file_size} bytes")
                return DownloadResult(
                    success=False,
                    error=f"File size exceeds limit ({MAX_FILE_SIZE}MB)"
                )
            
            # Update task info
            if task:
                task.file_name = file_name
                task.file_path = file_path
                task.size = file_size
            
            # Progress callback
            last_update = 0
            download_start = time.time()
            
            async def progress(current, total):
                nonlocal last_update, download_start
                
                if task:
                    if task.status == TaskStatus.CANCELLED:
                        raise BatchCancel("Download cancelled")
                    elif task.status.value == "skipped":
                        raise Exception("Task skipped")
                
                now = time.time()
                if now - last_update < UPDATE_INTERVAL:
                    return

                
                last_update = now
                
                # Calculate speed and ETA
                elapsed = now - download_start
                if elapsed > 0 and current > 0:
                    speed = current / elapsed
                    
                    # Update task progress
                    if task and user_id:
                        queue_manager.update_task_progress(
                            user_id, current, total, "download", speed
                        )
                    
                    # Call progress callback
                    asyncio.create_task(
                        progress_callback(
                            current, total, client, message, "download", user_id
                        )
                    )
            
            # Download with retry
            downloaded_path = await self._download_with_retry(
                client, msg, file_path, progress
            )
            
            if not downloaded_path or not os.path.exists(downloaded_path):
                return DownloadResult(
                    success=False,
                    error="Download failed - file not found"
                )
            
            download_time = time.time() - start_time
            
            # Update metrics
            usage_stats.increment("total_downloads")
            usage_stats.add("total_bandwidth", file_size)
            
            # Log to database
            await db.log_download(
                user_id=task.from_user_id if task else 0,
                file_name=file_name,
                file_size=file_size,
                file_type=task.file_type if task else "unknown",
                success=True,
                download_time=download_time
            )
            
            logger.info(f"Download completed: {file_name} ({download_time:.2f}s)")
            
            return DownloadResult(
                success=True,
                file_path=downloaded_path,
                file_size=file_size,
                download_time=download_time
            )
            
        except BatchCancel:
            logger.info(f"Download cancelled: {download_id}")
            return DownloadResult(
                success=False,
                error="Download cancelled by user"
            )
            
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"Flood wait: {wait_time}s")
            return DownloadResult(
                success=False,
                error=f"Rate limit exceeded. Wait {wait_time}s"
            )
            
        except Exception as e:
            logger.error(f"Download error: {e}", exc_info=True)
            
            # Log error to database
            if task:
                await db.log_download(
                    user_id=task.from_user_id,
                    success=False,
                    error_message=str(e)[:500]
                )
            
            return DownloadResult(
                success=False,
                error=str(e)[:200]
            )
    
    @retry_on_error(max_retries=3, delay=2)
    async def _download_with_retry(self, client, msg, file_path, progress):
        """Download file with retry logic"""
        return await client.download_media(
            msg,
            file_name=file_path,
            progress=progress
        )
    
    async def _generate_filename(self, msg: Message, task: Optional[FileTask]) -> str:
        """Generate filename for download"""
        if task and task.file_name:
            return task.file_name
        
        msg_type = self._get_message_type(msg)
        
        # Try to get original filename
        media = getattr(msg, msg_type.lower(), None) if msg_type else None
        if media and hasattr(media, 'file_name') and media.file_name:
            return media.file_name
        
        # Generate filename
        timestamp = int(time.time())
        if msg_type == FileType.TEXT:
            return f"text_message_{timestamp}.txt"
        elif msg_type == FileType.PHOTO:
            return f"photo_{timestamp}.jpg"
        elif msg_type == FileType.VIDEO:
            # Check mime type for proper extension
            mime = getattr(media, "mime_type", "") if media else ""
            if "x-matroska" in mime:
                return f"video_{timestamp}.mkv"
            elif "webm" in mime:
                return f"video_{timestamp}.webm"
            return f"video_{timestamp}.mp4"
        elif msg_type == FileType.AUDIO:
            mime = getattr(media, "mime_type", "") if media else ""
            if "ogg" in mime:
                return f"audio_{timestamp}.ogg"
            elif "wav" in mime:
                return f"audio_{timestamp}.wav"
            return f"audio_{timestamp}.mp3"
        elif msg_type == FileType.VOICE:
            return f"voice_{timestamp}.ogg"
        elif msg_type == FileType.ANIMATION:
            mime = getattr(media, "mime_type", "") if media else ""
            if "gif" in mime:
                return f"animation_{timestamp}.gif"
            return f"animation_{timestamp}.mp4"
        elif msg_type == FileType.STICKER:
            if media and getattr(media, "is_animated", False):
                return f"sticker_{timestamp}.tgs"
            elif media and getattr(media, "is_video", False):
                return f"sticker_{timestamp}.webm"
            return f"sticker_{timestamp}.webp"
        
        return f"file_{timestamp}.bin"
    
    def _get_message_type(self, msg: Message) -> Optional[FileType]:
        """Get message type enum"""
        if msg.document:
            return FileType.DOCUMENT
        elif msg.video:
            return FileType.VIDEO
        elif msg.animation:
            return FileType.ANIMATION
        elif msg.sticker:
            return FileType.STICKER
        elif msg.voice:
            return FileType.VOICE
        elif msg.audio:
            return FileType.AUDIO
        elif msg.photo:
            return FileType.PHOTO
        elif msg.text:
            return FileType.TEXT
        return None
    
    def _get_file_size(self, msg: Message) -> int:
        """Get file size from message"""
        if msg.document:
            return msg.document.file_size or 0
        elif msg.video:
            return msg.video.file_size or 0
        elif msg.audio:
            return msg.audio.file_size or 0
        elif msg.photo:
            return msg.photo.file_size or 0
        elif msg.voice:
            return msg.voice.file_size or 0
        elif msg.animation:
            return msg.animation.file_size or 0
        return 0
    
    async def cleanup_old_downloads(self, max_age: int = 3600) -> int:
        """Clean up old download files"""
        cleaned = 0
        try:
            for filename in os.listdir(self.downloads_dir):
                file_path = os.path.join(self.downloads_dir, filename)
                if os.path.isfile(file_path):
                    # Check file age
                    file_age = time.time() - os.path.getmtime(file_path)
                    if file_age > max_age:
                        await safe_delete_file(file_path)
                        cleaned += 1
            
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} old download files")
                
        except Exception as e:
            logger.error(f"Error cleaning downloads: {e}")
        
        return cleaned


# Global download service instance
download_service = DownloadService()