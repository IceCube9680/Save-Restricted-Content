"""
Progress Display Manager
Handles updating message edits for download/upload progress with enhanced features
"""

import time
import asyncio
import contextlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple, List, AsyncIterator
from enum import Enum
from functools import lru_cache

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageNotModified, FloodWait

from config import WAITING_TIME
from plugins.core.constants import TaskStatus
from plugins.core.utils import get_logger, humanbytes, time_formatter, truncate_text
from plugins.core.animations import ProgressAnimations
from plugins.services.queue_manager import queue_manager

logger = get_logger(__name__)


@dataclass
class ProgressDisplayConfig:
    """Configuration constants for progress display"""
    MAX_FILENAME_LENGTH: int = 30
    UPDATE_INTERVAL: float = 3.0
    PROGRESS_BAR_LENGTH: int = 20
    BATCH_BAR_LENGTH: int = 20
    DATE_FORMAT: str = "%d %b %Y"
    TIME_FORMAT: str = "%I:%M:%S %p"
    RECREATE_ON_INVALID: bool = True
    AUTO_CLEANUP_DELAY: float = WAITING_TIME
    SPINNER_UPDATE_INTERVAL: float = 0.25
    MAX_SPINNER_AGE: int = 3600  # 1 hour
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 2.0
    METRICS_ENABLED: bool = True
    TIMEZONE_OFFSET: float = 0.0  # Offset in hours from server time


class ProgressBarStyle(str, Enum):
    """Available progress bar styles"""
    DEFAULT = "default"
    BLOCKS = "blocks"
    ARROWS = "arrows"
    DOTS = "dots"
    COMPACT = "compact"
    MODERN = "modern"
    ARROW = "arrow"
    GRADIENT = "gradient"
    BLOCK = "block"
    CIRCLE = "circle"
    SQUARE = "square"
    
    @classmethod
    def get_style(cls, style: str) -> Tuple[str, str]:
        """Get fill and empty characters for style"""
        styles = {
            "default": ("█", "░"),
            "blocks": ("█", "▁"),
            "arrows": ("▶", "○"),
            "dots": ("●", "○"),
            "compact": ("■", "□"),
            "modern": ("█", "░"),
            "arrow": ("▶", "▷"),
            "gradient": ("█", "▒"),
            "block": ("█", "▁"),
            "circle": ("●", "○"),
            "square": ("■", "□")
        }
        return styles.get(style, styles["default"])


class ProgressSpinner:
    """Manages animated spinner states"""
    
    _spinners = {
        "dots": ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
        "line": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "arrow": ["←", "↖", "↑", "↗", "→", "↘", "↓", "↙"],
        "simple": ["⡏", "⡟", "⡿", "⢿", "⣻", "⣽", "⣾", "⣷"]
    }
    
    def __init__(self, style: str = "simple", update_interval: float = 0.25):
        self.style = style
        self.spinner_set = self._spinners.get(style, self._spinners["simple"])
        self.update_interval = update_interval
        self.last_update = 0
        self.index = 0
    
    def get_spinner(self) -> str:
        """Get next spinner character"""
        now = time.time()
        if now - self.last_update > self.update_interval:
            self.index = (self.index + 1) % len(self.spinner_set)
            self.last_update = now
        return self.spinner_set[self.index]
    
    @property
    def last_activity(self) -> float:
        """Get last update timestamp"""
        return self.last_update


class ProgressBarFactory:
    """Factory for creating styled progress bars"""
    
    @staticmethod
    @lru_cache(maxsize=32)
    def _get_style_chars(style: str) -> Tuple[str, str]:
        """Cached style character lookup"""
        return ProgressBarStyle.get_style(style)
    
    @staticmethod
    def create_bar(percentage: float, style: str = "default", length: int = 20) -> str:
        """Create a progress bar with specified style and length"""
        if percentage < 0:
            percentage = 0
        elif percentage > 100:
            percentage = 100
            
        fill_char, empty_char = ProgressBarFactory._get_style_chars(style)
        filled = int(percentage / 100 * length)
        filled = min(filled, length)
        
        return fill_char * filled + empty_char * (length - filled)
    
    @staticmethod
    def create_percentage_bar(percentage: float, style: str = "default") -> str:
        """Create compact bar with percentage"""
        bar = ProgressBarFactory.create_bar(percentage, style, 10)
        return f"{bar} {percentage:.1f}%"


class ProgressTemplate:
    """Message templates for progress display"""
    
    FORWARDING_BATCH_HEADER = """
⚡ **BATCH PROCESSING** ⚡
━━━━━━━━━━━━━━━━━━━━
📊 **Batch Progress:** `{batch_progress:.1f}%`
{batch_bar}

📁 **Total Files:** `{total}`
✅ **Completed:** `{completed}`
⏳ **Remaining Files:** `{remaining}`
❌ **Failed / Skipped:** `{failed}`

⏱️ **Total Time:** `{elapsed_time}`
⏳ **Remaining Time:** `{remaining_time}`
🎯 **Est Finish Time:** `{est_time}`
🚀 **Batch Speed:** `{batch_speed:.2f}` files/min

━━━━━━━━━━━━━━━━━━━━
⏰ **Updated:** `{time}`
📅 **Date:** `{date}`
"""

    FORWARDING_SINGLE_HEADER = """
⚡ **PROCESSING** ⚡
━━━━━━━━━━━━━━━━━━━━
📊 **Progress:** `100.0%`
{bar}

📁 **Total Files:** `1`
✅ **Completed:** `1`
⏳ **Remaining Files:** `0`

⏱️ **Total Time:** `{elapsed_time}`
⏳ **Remaining Time:** `0s`
🎯 **Est Finish Time:** `{est_time}`

━━━━━━━━━━━━━━━━━━━━
⏰ **Updated:** `{time}`
📅 **Date:** `{date}`
"""

    BATCH_HEADER = """
⚡ **BATCH PROCESSING** ⚡
━━━━━━━━━━━━━━━━━━━━
📊 **Batch Progress:** `{batch_progress:.1f}%`
{batch_bar}

📁 **Total Files:** `{total}`
✅ **Completed:** `{completed}`
⏳ **Remaining Files:** `{remaining}`
❌ **Failed / Skipped:** `{failed}`

⏱️ **Total Time:** `{elapsed_time}`
⏳ **Remaining Time:** `{remaining_time}`
🎯 **Est Finish Time:** `{est_time}`
🚀 **Batch Speed:** `{batch_speed:.2f}` files/min

━━━━━━━━━━━━━━━━━━━━
📁 **CURRENT FILE:** `{position}`
{status_emoji} **Status:** `{status}`
🎬 **File:** `{filename}`
{spinner} **Progress:** `{percentage:.1f}%`
{bar}
🆔 **Message ID:** `{msgid}`

📦 **Size:** `{size}`
⚡ **Speed:** `{speed}`
⏱️ **File ETA:** `{eta}`

━━━━━━━━━━━━━━━━━━━━
⏰ **Updated:** `{time}`
📅 **Date:** `{date}`
"""
    
    SINGLE_HEADER = """
{status_emoji} **{status}**
━━━━━━━━━━━━━━━━━━━━
🎬 **File:** `{filename}`
{spinner} **Progress:** `{percentage:.1f}%`
{bar}

🆔 **Message ID:** `{msgid}`
📦 **Size:** `{size}`
⚡ **Speed:** `{speed}`

⏱️ **Total Time:** `{elapsed_time}`
⏳ **Remaining Time:** `{remaining_time}`
🎯 **Est Finish Time:** `{est_time}`

━━━━━━━━━━━━━━━━━━━━
⏰ **Updated:** `{time}`
📅 **Date:** `{date}`
"""
    
    BATCH_STATUS = """
🔄 **BATCH PROCESSING**
━━━━━━━━━━━━━━━━━━━━
📊 **Overall Progress:** `{batch_progress:.1f}%`
{bar}

📁 **Total Files:** `{total}`
✅ **Forwarded / Done:** `{completed}`
⏳ **Remaining Files:** `{remaining}`
❌ **Failed / Skipped:** `{failed}`

⏱️ **Total Time:** `{elapsed_time}`
⏳ **Remaining Time:** `{remaining_time}`
🎯 **Est Finish Time:** `{est_time}`
🚀 **Batch Speed:** `{batch_speed:.2f}` files/min

📊 **Status:** `{status}`
━━━━━━━━━━━━━━━━━━━━
⏰ **Updated:** `{time}`
📅 **Date:** `{date}`
"""
    
    RECOVERY_MESSAGE = "🔄 **Recovering progress display...**"


@dataclass
class ProgressMetrics:
    """Collect metrics for progress display performance"""
    updates: int = 0
    errors: int = 0
    recreations: int = 0
    total_update_time: float = 0
    flood_waits: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[float] = None
    
    @property
    def avg_update_time(self) -> float:
        """Calculate average update time"""
        if self.updates == 0:
            return 0
        return self.total_update_time / self.updates
    
    def record_update(self, duration: float) -> None:
        """Record successful update"""
        self.updates += 1
        self.total_update_time += duration
    
    def record_error(self, error: str) -> None:
        """Record error occurrence"""
        self.errors += 1
        self.last_error = error
        self.last_error_time = time.time()
    
    def record_recreation(self) -> None:
        """Record message recreation"""
        self.recreations += 1
    
    def record_flood_wait(self) -> None:
        """Record flood wait occurrence"""
        self.flood_waits += 1
    
    def reset(self) -> None:
        """Reset all metrics"""
        self.updates = 0
        self.errors = 0
        self.recreations = 0
        self.total_update_time = 0
        self.flood_waits = 0
        self.last_error = None
        self.last_error_time = None


class ProgressDisplayManager:
    """Manages progress display updates with rate limiting and error recovery"""
    
    def __init__(self, update_interval: float = ProgressDisplayConfig.UPDATE_INTERVAL):
        self.update_interval = update_interval
        self.active_displays: Dict[int, Dict[str, Any]] = {}
        self.spinners: Dict[int, ProgressSpinner] = {}
        self.metrics: Dict[int, ProgressMetrics] = {}
        self.config = ProgressDisplayConfig()
    
    def _get_metrics(self, user_id: int) -> ProgressMetrics:
        """Get or create metrics for user"""
        if user_id not in self.metrics:
            self.metrics[user_id] = ProgressMetrics()
        return self.metrics[user_id]
    
    def _get_spinner(self, user_id: int) -> str:
        """Get or create spinner for user"""
        if user_id not in self.spinners:
            self.spinners[user_id] = ProgressSpinner(
                "simple", 
                self.config.SPINNER_UPDATE_INTERVAL
            )
        return self.spinners[user_id].get_spinner()
    
    def _validate_queue_state(self, queue) -> bool:
        """Validate queue has required attributes"""
        if not queue:
            return False
        required_attrs = ['progress_message_id', 'chat_id', 'last_update_time']
        return all(hasattr(queue, attr) for attr in required_attrs)
    
    async def progress_callback(self, current: int, total: int, client: Client, 
                               message: object, status: str, user_id: int) -> None:
        """Callback function for download/upload progress"""
        await self.update_progress_display(client, user_id)
    
    async def update_progress_display(self, client: Client, user_id: int, 
                                     force: bool = False) -> bool:
        """Update the progress message for a user with rate limiting"""
        queue = queue_manager.get_queue(user_id)
        
        if not self._can_update_display(queue, force):
            return False
        
        queue.last_update_time = time.time()
        
        # Retry logic without tenacity
        last_error = None
        for attempt in range(self.config.MAX_RETRIES):
            try:
                await self._perform_update(client, user_id, queue)
                return True
            except MessageNotModified:
                return False
            except FloodWait as e:
                self._get_metrics(user_id).record_flood_wait()
                await asyncio.sleep(e.value)
                return False
            except Exception as e:
                last_error = e
                self._get_metrics(user_id).record_error(str(e))
                
                if attempt < self.config.MAX_RETRIES - 1:
                    wait_time = self.config.RETRY_DELAY * (2 ** attempt)  # Exponential backoff
                    logger.debug(f"Retry {attempt + 1}/{self.config.MAX_RETRIES} for user {user_id} in {wait_time}s")
                    await asyncio.sleep(wait_time)
                else:
                    await self._handle_update_error(client, user_id, queue, last_error)
                    return False
        
        return False
    
    def _can_update_display(self, queue, force: bool) -> bool:
        """Check if display can be updated"""
        if not self._validate_queue_state(queue):
            return False
        if not force and queue.last_update_time:
            if time.time() - queue.last_update_time < self.update_interval:
                return False
        return True
    
    async def _perform_update(self, client: Client, user_id: int, queue) -> None:
        """Perform the actual message update"""
        start_time = time.time()
        task = queue.current_task
        
        # Generate message content
        if task:
            text = self._generate_task_progress_text(queue, task, user_id)
        else:
            text = self._generate_batch_status_text(queue)
        
        # Generate buttons
        buttons = self._generate_control_buttons(user_id, queue)
        
        await client.edit_message_text(
            chat_id=queue.chat_id,
            message_id=queue.progress_message_id,
            text=text,
            reply_markup=buttons
        )
        
        # Record metrics
        if self.config.METRICS_ENABLED:
            self._get_metrics(user_id).record_update(time.time() - start_time)
    
    async def _handle_update_error(self, client: Client, user_id: int, 
                                  queue, error: Exception) -> None:
        """Handle errors during progress update"""
        error_str = str(error)
        logger.debug(f"Error updating progress display for user {user_id}: {error_str}")
        
        if self.config.RECREATE_ON_INVALID:
            if "MESSAGE_ID_INVALID" in error_str or "CHAT_ID_INVALID" in error_str:
                await self._recreate_progress_message(client, user_id, queue)
                self._get_metrics(user_id).record_recreation()
            elif "MESSAGE_NOT_MODIFIED" not in error_str:
                logger.warning(f"Unexpected error updating progress: {error_str}")
    
    async def _recreate_progress_message(self, client: Client, user_id: int, queue) -> bool:
        """Attempt to recreate a deleted progress message"""
        try:
            # Send new progress message
            new_msg = await client.send_message(
                chat_id=queue.chat_id,
                text=ProgressTemplate.RECOVERY_MESSAGE
            )
            
            # Update queue with new message ID
            queue.progress_message_id = new_msg.id
            
            # Force update immediately
            await self.update_progress_display(client, user_id, force=True)
            logger.info(f"Recreated progress message for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to recreate progress message for user {user_id}: {e}")
            return False
    
    def _generate_task_progress_text(self, queue, task, user_id: int) -> str:
        """Generate progress text for active task"""
        # Determine if this task/batch is forwarding or download/upload
        if task.status in [TaskStatus.DOWNLOADING, TaskStatus.UPLOADING] or getattr(task.status, 'value', '') in ["downloading", "uploading"]:
            is_forwarding = False
        elif task.status == TaskStatus.FORWARDING or getattr(task.status, 'value', '') == "forwarding":
            is_forwarding = True
        else:
            is_forwarding = getattr(queue, 'is_forwarding_mode', True)
        
        # Time info
        now = datetime.now() + timedelta(hours=self.config.TIMEZONE_OFFSET)
        time_str = now.strftime(self.config.TIME_FORMAT)
        date_str = now.strftime(self.config.DATE_FORMAT)
        
        # Format filename
        fname = task.file_name or f"Message #{task.msgid}"
        fname = truncate_text(fname, self.config.MAX_FILENAME_LENGTH)
        
        if queue.total_tasks > 1:
            if is_forwarding:
                # Forwarding allowed: Show ONLY overall batch progress (no current file progress bar)
                return self._generate_forwarding_batch_text(
                    queue, task, fname, time_str, date_str
                )
            else:
                # Restricted content (download/upload): Show BOTH current file progress and overall batch progress
                percentage = min(100.0, max(0.0, task.progress))
                bar = ProgressBarFactory.create_bar(
                    percentage, 
                    style=queue.progress_style,
                    length=self.config.PROGRESS_BAR_LENGTH
                )
                status_emoji = ProgressAnimations.get_status_emoji(task.status.value)
                spinner = self._get_spinner(user_id)
                size = humanbytes(task.size) if task.size > 0 else "N/A"
                speed = humanbytes(task.speed) + "/s" if task.speed > 0 else "0 B/s"
                eta = self._format_eta_with_time(task.eta)

                return self._generate_batch_mode_text(
                    queue, task, fname, spinner, status_emoji, 
                    bar, percentage, speed, size, eta, time_str, date_str
                )
        else:
            elapsed = time.time() - (task.start_time or queue.batch_start_time or time.time())
            elapsed_time = time_formatter(elapsed)
            size = humanbytes(task.size) if task.size > 0 else "N/A"
            
            if is_forwarding:
                bar = ProgressBarFactory.create_bar(
                    100.0, 
                    style=queue.progress_style,
                    length=self.config.PROGRESS_BAR_LENGTH
                )
                return ProgressTemplate.FORWARDING_SINGLE_HEADER.format(
                    bar=bar,
                    elapsed_time=elapsed_time,
                    est_time=time_str,
                    time=time_str,
                    date=date_str
                )
            
            percentage = min(100.0, max(0.0, task.progress))
            bar = ProgressBarFactory.create_bar(
                percentage, 
                style=queue.progress_style,
                length=self.config.PROGRESS_BAR_LENGTH
            )
            status_emoji = ProgressAnimations.get_status_emoji(task.status.value)
            spinner = self._get_spinner(user_id)
            
            if task.status.value == "completed":
                speed = "Done"
                remaining_time = "0s"
                est_time = time_str
            else:
                speed = humanbytes(task.speed) + "/s" if task.speed > 0 else "0 B/s"
                remaining_time = time_formatter(task.eta) if task.eta > 0 else "Calculating..."
                est_time = (now + timedelta(seconds=task.eta)).strftime(self.config.TIME_FORMAT) if task.eta > 0 else "Calculating..."

            return ProgressTemplate.SINGLE_HEADER.format(
                status_emoji=status_emoji,
                status=task.status.value.upper(),
                filename=fname,
                spinner=spinner,
                percentage=percentage,
                bar=bar,
                msgid=task.msgid,
                speed=speed,
                size=size,
                elapsed_time=elapsed_time,
                remaining_time=remaining_time,
                est_time=est_time,
                time=time_str,
                date=date_str
            )
    
    def _generate_forwarding_batch_text(self, queue, task, fname: str, time_str: str, date_str: str) -> str:
        """Generate overall-only progress text for forwarding mode"""
        batch_progress = queue.get_batch_progress()
        batch_bar = ProgressBarFactory.create_bar(
            batch_progress, 
            style=queue.progress_style,
            length=self.config.BATCH_BAR_LENGTH
        )
        
        elapsed = time.time() - queue.batch_start_time if queue.batch_start_time else 0
        elapsed_time = time_formatter(elapsed)
        batch_speed = queue.completed_tasks / elapsed if elapsed > 0 else 0
        
        remaining = max(0, queue.total_tasks - queue.completed_tasks)
        batch_eta_seconds = queue.get_batch_eta()
        if (not batch_eta_seconds or batch_eta_seconds <= 0) and remaining > 0:
            if batch_speed > 0:
                batch_eta_seconds = remaining / batch_speed
            else:
                batch_eta_seconds = remaining * 1.5
            
        remaining_time = time_formatter(batch_eta_seconds) if batch_eta_seconds > 0 else "0s"
        
        now = datetime.now() + timedelta(hours=self.config.TIMEZONE_OFFSET)
        if batch_eta_seconds > 0:
            completion_time = now + timedelta(seconds=batch_eta_seconds)
            est_time = completion_time.strftime(self.config.TIME_FORMAT)
        else:
            est_time = time_str if remaining == 0 else "Calculating..."
        
        current_position = min(queue.completed_tasks + 1, queue.total_tasks)
        task_position = f"{current_position}/{queue.total_tasks}"
        
        return ProgressTemplate.FORWARDING_BATCH_HEADER.format(
            batch_progress=batch_progress,
            batch_bar=batch_bar,
            total=queue.total_tasks,
            completed=queue.completed_tasks,
            remaining=remaining,
            failed=queue.failed_tasks,
            elapsed_time=elapsed_time,
            remaining_time=remaining_time,
            est_time=est_time,
            batch_speed=batch_speed * 60,
            time=time_str,
            date=date_str
        )
    
    def _generate_batch_mode_text(self, queue, task, fname: str, spinner: str,
                                 status_emoji: str, bar: str, percentage: float,
                                 speed: str, size: str, eta: str, 
                                 time_str: str, date_str: str) -> str:
        """Generate text for batch processing mode"""
        batch_progress = queue.get_batch_progress()
        batch_bar = ProgressBarFactory.create_bar(
            batch_progress, 
            style=queue.progress_style,
            length=self.config.BATCH_BAR_LENGTH
        )
        
        # Calculate batch speed and elapsed time
        elapsed = time.time() - queue.batch_start_time if queue.batch_start_time else 0
        elapsed_time = time_formatter(elapsed)
        batch_speed = queue.completed_tasks / elapsed if elapsed > 0 else 0
        
        remaining = max(0, queue.total_tasks - queue.completed_tasks)
        batch_eta_seconds = queue.get_batch_eta()
        if (not batch_eta_seconds or batch_eta_seconds <= 0) and remaining > 0:
            if batch_speed > 0:
                batch_eta_seconds = remaining / batch_speed
            elif task and task.speed > 0 and task.size > 0:
                time_per_file = task.size / task.speed
                batch_eta_seconds = remaining * time_per_file
            else:
                batch_eta_seconds = remaining * 1.5
            
        remaining_time = time_formatter(batch_eta_seconds) if batch_eta_seconds > 0 else "0s"
        
        now = datetime.now() + timedelta(hours=self.config.TIMEZONE_OFFSET)
        if batch_eta_seconds > 0:
            completion_time = now + timedelta(seconds=batch_eta_seconds)
            est_time = completion_time.strftime(self.config.TIME_FORMAT)
        else:
            est_time = time_str if remaining == 0 else "Calculating..."
        
        # Get task position
        current_position = min(queue.completed_tasks + 1, queue.total_tasks)
        task_position = f"{current_position}/{queue.total_tasks}"
        
        return ProgressTemplate.BATCH_HEADER.format(
            batch_progress=batch_progress,
            batch_bar=batch_bar,
            msgid=task.msgid,
            completed=queue.completed_tasks,
            total=queue.total_tasks,
            remaining=remaining,
            failed=queue.failed_tasks,
            elapsed_time=elapsed_time,
            remaining_time=remaining_time,
            est_time=est_time,
            batch_speed=batch_speed * 60,
            position=task_position,
            status_emoji=status_emoji,
            status=task.status.value.upper(),
            filename=fname,
            spinner=spinner,
            percentage=percentage,
            bar=bar,
            speed=speed,
            size=size,
            eta=eta,
            time=time_str,
            date=date_str
        )
    
    def _generate_batch_status_text(self, queue) -> str:
        """Generate status text for batch when no active task"""
        now = datetime.now() + timedelta(hours=self.config.TIMEZONE_OFFSET)
        time_str = now.strftime(self.config.TIME_FORMAT)
        date_str = now.strftime(self.config.DATE_FORMAT)

        if getattr(queue, 'is_forwarding_mode', True):
            return self._generate_forwarding_batch_text(queue, None, "", time_str, date_str)

        batch_progress = queue.get_batch_progress()
        bar = ProgressBarFactory.create_bar(
            batch_progress, 
            style=queue.progress_style,
            length=self.config.PROGRESS_BAR_LENGTH
        )
        
        elapsed = time.time() - queue.batch_start_time if queue.batch_start_time else 0
        elapsed_time = time_formatter(elapsed)
        batch_speed = queue.completed_tasks / elapsed if elapsed > 0 else 0
        
        remaining = max(0, queue.total_tasks - queue.completed_tasks)
        batch_eta_seconds = queue.get_batch_eta()
        if (not batch_eta_seconds or batch_eta_seconds <= 0) and remaining > 0:
            if batch_speed > 0:
                batch_eta_seconds = remaining / batch_speed
            else:
                batch_eta_seconds = remaining * 1.5
                
        remaining_time = time_formatter(batch_eta_seconds) if batch_eta_seconds > 0 else "0s"
        
        if batch_eta_seconds > 0:
            completion_time = now + timedelta(seconds=batch_eta_seconds)
            est_time = completion_time.strftime(self.config.TIME_FORMAT)
        else:
            est_time = time_str if remaining == 0 else "Completed"
        
        return ProgressTemplate.BATCH_STATUS.format(
            batch_progress=batch_progress,
            bar=bar,
            total=queue.total_tasks,
            completed=queue.completed_tasks,
            remaining=remaining,
            failed=queue.failed_tasks,
            elapsed_time=elapsed_time,
            remaining_time=remaining_time,
            est_time=est_time,
            batch_speed=batch_speed * 60,
            status="Waiting for next task...",
            time=time_str,
            date=date_str
        )
    
    def _format_eta_with_time(self, eta_seconds: float) -> str:
        """Format ETA with actual completion time"""
        if not eta_seconds or eta_seconds <= 0:
            return "Calculating..."
        
        eta_formatted = time_formatter(eta_seconds)
        now = datetime.now() + timedelta(hours=self.config.TIMEZONE_OFFSET)
        completion_time = now + timedelta(seconds=eta_seconds)
        time_str = completion_time.strftime("%I:%M %p")
        
        return f"{eta_formatted} (~{time_str})"
    
    def _generate_control_buttons(self, user_id: int, queue) -> InlineKeyboardMarkup:
        """Generate control buttons with dynamic states"""
        buttons = []
        
        # Control row
        if queue.is_paused:
            control_row = [
                InlineKeyboardButton("▶️ Resume", callback_data=f"resume_{user_id}"),
                InlineKeyboardButton("⏹️ Cancel", callback_data=f"cancel_{user_id}")
            ]
        else:
            control_row = [
                InlineKeyboardButton("⏸️ Pause", callback_data=f"pause_{user_id}"),
                InlineKeyboardButton("⏹️ Cancel", callback_data=f"cancel_{user_id}")
            ]
        buttons.append(control_row)
        
        # Info row
        buttons.append([
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{user_id}"),
            InlineKeyboardButton("ℹ️ Details", callback_data=f"details_{user_id}")
        ])
        
        # Additional controls for batch mode
        if queue.total_tasks > 1:
            buttons.append([
                InlineKeyboardButton("⏭️ Skip", callback_data=f"skip_{user_id}"),
                InlineKeyboardButton("📋 Queue", callback_data=f"queue_{user_id}")
            ])
        
        return InlineKeyboardMarkup(buttons)
    
    def cleanup_inactive_spinners(self, max_age: Optional[int] = None) -> int:
        """Remove spinners for inactive users"""
        if max_age is None:
            max_age = self.config.MAX_SPINNER_AGE
            
        current_time = time.time()
        to_remove = []
        
        for user_id, spinner in self.spinners.items():
            if current_time - spinner.last_activity > max_age:
                to_remove.append(user_id)
        
        for user_id in to_remove:
            del self.spinners[user_id]
                
        return len(to_remove)
    
    def get_user_metrics(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get metrics for a specific user"""
        if user_id not in self.metrics:
            return None
        
        metrics = self.metrics[user_id]
        return {
            "updates": metrics.updates,
            "errors": metrics.errors,
            "recreations": metrics.recreations,
            "avg_update_time": f"{metrics.avg_update_time * 1000:.2f}ms",
            "flood_waits": metrics.flood_waits,
            "last_error": metrics.last_error,
            "last_error_time": (datetime.fromtimestamp(metrics.last_error_time)
                               .strftime(self.config.TIME_FORMAT) 
                               if metrics.last_error_time else None)
        }
    
    def reset_user_metrics(self, user_id: int) -> None:
        """Reset metrics for a user"""
        if user_id in self.metrics:
            self.metrics[user_id].reset()
    
    @contextlib.asynccontextmanager
    async def batch_progress_context(self, client: Client, user_id: int):
        """Context manager for batch progress display with auto-cleanup"""
        queue = queue_manager.get_queue(user_id)
        
        if not queue or not queue.progress_message_id:
            yield
            return
        
        try:
            yield
        except Exception as e:
            logger.error(f"Error during batch progress for user {user_id}: {e}")
            self._get_metrics(user_id).record_error(str(e))
            raise
        finally:
            # Clean up progress message after delay
            await asyncio.sleep(self.config.AUTO_CLEANUP_DELAY)
            queue = queue_manager.get_queue(user_id)
            
            if queue and queue.progress_message_id:
                try:
                    await client.delete_messages(
                        queue.chat_id,
                        queue.progress_message_id
                    )
                    logger.info(f"Cleaned up progress message for user {user_id}")
                except Exception as e:
                    logger.debug(f"Failed to delete progress message for {user_id}: {e}")
            
            # Clean up spinner
            if user_id in self.spinners:
                del self.spinners[user_id]
    
    async def watch_progress(self, client: Client, user_id: int, 
                            interval: float = 1.0) -> AsyncIterator:
        """Async iterator for monitoring progress updates"""
        while True:
            queue = queue_manager.get_queue(user_id)
            
            if not queue or queue.is_completed:
                break
                
            await self.update_progress_display(client, user_id, force=False)
            await asyncio.sleep(interval)
            yield queue


# Global instance
progress_display_manager = ProgressDisplayManager()

# Maintain backward compatibility
progress_callback = progress_display_manager.progress_callback
update_progress_display = progress_display_manager.update_progress_display