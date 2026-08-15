"""
Upload Service
"""

import os
import time
import asyncio
from typing import Optional, Dict
from datetime import datetime

from pyrogram import Client, enums
from pyrogram.errors import FloodWait, WebpageCurlFailed
from pyrogram.types import Message

from config import UPLOAD_TIMEOUT, ENABLE_GLOBAL_CHANNEL, GLOBAL_CHANNEL_ID, ERROR_MESSAGE, UPDATE_INTERVAL
from database.mongodb import db
from plugins.core.models import FileTask, TaskStatus, BatchCancel, DownloadResult
from plugins.core.utils import get_logger, safe_delete_file
from plugins.monitoring.metrics import usage_stats
from plugins.progress_display import progress_callback
from plugins.services.queue_manager import queue_manager

logger = get_logger(__name__)


class UploadService:
    """Service for uploading files to Telegram"""
    
    def __init__(self):
        self.active_uploads: Dict[str, asyncio.Task] = {}
    
    async def upload_file(
        self,
        client: Client,
        file_path: str,
        msg: Message,
        msg_type: str,
        target_chat: int,
        reply_to_id: Optional[int],
        caption: Optional[str],
        thumb_path: Optional[str],
        task: Optional[FileTask] = None,
        user_id: Optional[int] = None
    ) -> DownloadResult:
        """Upload file with progress tracking"""
        upload_id = f"{task.from_user_id}_{task.msgid}" if task else f"temp_{time.time()}"
        start_time = time.time()
        
        try:
            # Check if upload is cancelled
            if task:
                if task.status == TaskStatus.CANCELLED:
                    raise BatchCancel("Upload cancelled by user")
                elif task.status.value == "skipped":
                    raise Exception("Task skipped by user")
            
            # Check if file exists
            if not os.path.isfile(file_path):
                return DownloadResult(
                    success=False,
                    error=f"File not found: {file_path}"
                )
            
            # Get file size
            file_size = os.path.getsize(file_path)
            
            # Progress callback
            last_update = 0
            upload_start = time.time()
            
            async def progress(current, total):
                nonlocal last_update, upload_start
                
                if task:
                    if task.status == TaskStatus.CANCELLED:
                        raise BatchCancel("Upload cancelled")
                    elif task.status.value == "skipped":
                        raise Exception("Task skipped")
                
                now = time.time()
                if now - last_update < UPDATE_INTERVAL:
                    return

                
                last_update = now
                
                # Calculate speed
                elapsed = now - upload_start
                if elapsed > 0 and current > 0:
                    speed = current / elapsed
                    
                    # Update task progress
                    if task and user_id:
                        queue_manager.update_task_progress(
                            user_id, current, total, "upload", speed
                        )
                    
                    # Call progress callback
                    asyncio.create_task(
                        progress_callback(
                            current, total, client, msg, "upload", user_id
                        )
                    )
            
            # Update task status
            if task:
                task.status = TaskStatus.UPLOADING
            
            # Upload based on message type
            sent_msg = await self._upload_by_type(
                client, msg_type, file_path, target_chat,
                reply_to_id, caption, thumb_path, msg, progress
            )
            
            upload_time = time.time() - start_time
            
            if sent_msg:
                # Update metrics
                usage_stats.increment("total_uploads")
                
                # Forward to global channel if enabled
                if ENABLE_GLOBAL_CHANNEL and GLOBAL_CHANNEL_ID:
                    try:
                        await self._upload_by_type(
                            client, msg_type, file_path, int(GLOBAL_CHANNEL_ID),
                            None, caption, thumb_path, msg, None
                        )
                    except Exception as e:
                        logger.error(f"Failed to upload to global channel: {e}")
                
                logger.debug(f"Upload completed: {os.path.basename(file_path)} ({upload_time:.2f}s)")
                
                return DownloadResult(
                    success=True,
                    file_path=file_path,
                    file_size=file_size,
                    upload_time=upload_time
                )
            else:
                return DownloadResult(
                    success=False,
                    error="Upload failed - no response",
                    upload_time=upload_time
                )
                
        except BatchCancel:
            logger.info(f"Upload cancelled: {upload_id}")
            return DownloadResult(
                success=False,
                error="Upload cancelled by user"
            )
            
        except FloodWait as e:
            wait_time = e.value
            logger.warning(f"Flood wait during upload: {wait_time}s")
            return DownloadResult(
                success=False,
                error=f"Rate limit exceeded. Wait {wait_time}s"
            )
            
        except Exception as e:
            logger.error(f"Upload error: {e}", exc_info=True)
            return DownloadResult(
                success=False,
                error=str(e)[:200]
            )
    
    async def _upload_by_type(
        self, client, msg_type, file_path, chat_id,
        reply_to_id, caption, thumb_path, original_msg, progress
    ):
        """Upload file based on message type"""
        
        upload_methods = {
            "Document": client.send_document,
            "Video": client.send_video,
            "Photo": client.send_photo,
            "Audio": client.send_audio,
            "Animation": client.send_animation,
            "Voice": client.send_voice,
            "Sticker": client.send_sticker,
        }
        
        if msg_type not in upload_methods:
            return None
        
        method = upload_methods[msg_type]
        
        # Common parameters
        kwargs = {
            "chat_id": chat_id,
            "caption": caption,
            "reply_to_message_id": reply_to_id,
            "parse_mode": enums.ParseMode.HTML
        }
        
        # Add type-specific parameters
        if msg_type == "Document":
            kwargs["document"] = file_path
            kwargs["thumb"] = thumb_path
        elif msg_type == "Video":
            kwargs["video"] = file_path
            kwargs["thumb"] = thumb_path
            if hasattr(original_msg, 'video') and original_msg.video:
                kwargs["duration"] = original_msg.video.duration
                kwargs["width"] = original_msg.video.width
                kwargs["height"] = original_msg.video.height
        elif msg_type == "Photo":
            kwargs["photo"] = file_path
        elif msg_type == "Audio":
            kwargs["audio"] = file_path
            kwargs["thumb"] = thumb_path
            if hasattr(original_msg, 'audio') and original_msg.audio:
                kwargs["duration"] = original_msg.audio.duration
                kwargs["performer"] = original_msg.audio.performer
                kwargs["title"] = original_msg.audio.title
        elif msg_type == "Animation":
            kwargs["animation"] = file_path
            kwargs["thumb"] = thumb_path
        elif msg_type == "Voice":
            kwargs["voice"] = file_path
            kwargs["duration"] = getattr(original_msg.voice, 'duration', 0)
        elif msg_type == "Sticker":
            kwargs["sticker"] = file_path
        
        # Add progress callback
        if progress:
            kwargs["progress"] = progress
        
        try:
            return await method(**kwargs)
        except WebpageCurlFailed:
            # Retry without thumbnail if it fails
            if "thumb" in kwargs:
                kwargs.pop("thumb")
                return await method(**kwargs)
            raise
    
    async def get_user_caption(self, user_id: int, original_caption: str = None) -> Optional[str]:
        """Get caption from user settings or use original"""
        try:
            user_caption = await db.get_caption(user_id)
            if user_caption:
                # Replace placeholders
                now = datetime.now()
                user_caption = user_caption.replace(
                    "{date}", now.strftime("%Y-%m-%d")
                ).replace(
                    "{time}", now.strftime("%H:%M:%S")
                ).replace(
                    "{user_id}", str(user_id)
                )
                return user_caption
        except Exception as e:
            logger.error(f"Error getting user caption: {e}")
        
        return original_caption
    
    async def get_user_thumbnail(self, user_id: int, client: Client) -> Optional[str]:
        """Get thumbnail from user settings"""
        try:
            user_thumb = await db.get_thumbnail(user_id)
            if not user_thumb:
                return None
            
            # Download thumbnail
            thumb_path = await client.download_media(user_thumb)
            
            # Validate thumbnail
            if thumb_path and os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
                return thumb_path
            else:
                await safe_delete_file(thumb_path)
                return None
                
        except Exception as e:
            logger.error(f"Error getting user thumbnail: {e}")
            return None


# Global upload service instance
upload_service = UploadService()