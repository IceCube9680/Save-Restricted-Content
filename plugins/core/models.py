"""
Data models for the bot
"""

import time
import random
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum

from .constants import TaskStatus
from .animations import DownloadAnimation
from config import DEFAULT_PROGRESS_STYLE


@dataclass
class FileTask:
    """Represents a single file download task"""
    
    message_id: int
    chat_id: int
    msgid: int
    from_user_id: int
    
    # Progress tracking
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    speed: float = 0.0
    eta: float = 0.0
    size: float = 0.0
    start_time: Optional[float] = None
    
    # File info
    file_type: Optional[str] = None
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    
    # Animation
    animation: DownloadAnimation = field(default_factory=DownloadAnimation)
    
    # Metadata
    retry_count: int = 0
    error_message: Optional[str] = None
    
    def update_progress(self, current: float, total: float) -> None:
        """Update task progress with speed calculation"""
        if total > 0:
            self.progress = (current / total) * 100
        else:
            self.progress = 0
            
        self.size = total
        
        if self.start_time:
            elapsed = time.time() - self.start_time
            if elapsed > 0:
                self.speed = current / elapsed
                if self.speed > 0 and total > current:
                    self.eta = (total - current) / self.speed
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "message_id": self.message_id,
            "chat_id": self.chat_id,
            "msgid": self.msgid,
            "from_user_id": self.from_user_id,
            "status": self.status.value,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "size": self.size,
            "file_type": self.file_type,
            "file_name": self.file_name,
            "file_path": self.file_path,
            "retry_count": self.retry_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FileTask':
        """Create from dictionary"""
        task = cls(
            message_id=data.get("message_id", 0),
            chat_id=data.get("chat_id", 0),
            msgid=data.get("msgid", 0),
            from_user_id=data.get("from_user_id", 0),
        )
        task.status = TaskStatus(data.get("status", "queued"))
        task.progress = data.get("progress", 0)
        task.speed = data.get("speed", 0)
        task.eta = data.get("eta", 0)
        task.size = data.get("size", 0)
        task.file_type = data.get("file_type")
        task.file_name = data.get("file_name")
        task.file_path = data.get("file_path")
        task.retry_count = data.get("retry_count", 0)
        return task


@dataclass
class UserQueue:
    """Represents a user's download queue"""
    
    user_id: int
    queue: List[FileTask] = field(default_factory=list)
    current_task: Optional[FileTask] = None
    batch_start_time: Optional[float] = None
    
    # Progress tracking
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    is_paused: bool = False
    
    # Display
    progress_message_id: Optional[int] = None
    chat_id: Optional[int] = None
    last_update_time: float = 0.0
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    progress_style: str = DEFAULT_PROGRESS_STYLE
    is_forwarding_mode: bool = True
    
    def __post_init__(self):
        """Initialize after creation"""
        if self.progress_style == "random":
            self.progress_style = random.choice(["gradient", "block", "circle", "modern"])
    
    def get_batch_progress(self) -> float:
        """Calculate overall batch progress percentage"""
        if self.total_tasks == 0:
            return 0.0
        
        completed = float(self.completed_tasks)
        if self.current_task:
            completed += min(100.0, max(0.0, self.current_task.progress)) / 100.0
        
        return min(100.0, max(0.0, (completed / self.total_tasks) * 100.0))
    
    def get_batch_eta(self) -> float:
        """Calculate batch ETA in seconds"""
        if self.total_tasks == 0 or self.completed_tasks >= self.total_tasks:
            return 0.0
        
        remaining_tasks = self.total_tasks - self.completed_tasks
        if not self.batch_start_time:
            return remaining_tasks * 2.0
        
        elapsed = time.time() - self.batch_start_time
        if self.completed_tasks > 0:
            avg_time_per_task = elapsed / self.completed_tasks
            return avg_time_per_task * remaining_tasks
        
        if self.current_task and self.current_task.progress > 5:
            est_single_task = elapsed / (self.current_task.progress / 100.0)
            return est_single_task * remaining_tasks
        
        return remaining_tasks * 2.0
    
    def get_remaining_tasks(self) -> int:
        """Get number of remaining tasks"""
        return self.total_tasks - self.completed_tasks
    
    def get_success_rate(self) -> float:
        """Calculate success rate percentage"""
        if self.total_tasks == 0:
            return 100.0
        
        successful = self.completed_tasks - self.failed_tasks
        return (successful / self.total_tasks) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "user_id": self.user_id,
            "current_task": self.current_task.to_dict() if self.current_task else None,
            "batch_start_time": self.batch_start_time,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "is_paused": self.is_paused,
            "progress_message_id": self.progress_message_id,
            "chat_id": self.chat_id,
            "metadata": self.metadata,
            "progress_style": self.progress_style,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserQueue':
        """Create from dictionary"""
        queue = cls(user_id=data.get("user_id", 0))
        if data.get("current_task"):
            queue.current_task = FileTask.from_dict(data["current_task"])
        queue.batch_start_time = data.get("batch_start_time")
        queue.total_tasks = data.get("total_tasks", 0)
        queue.completed_tasks = data.get("completed_tasks", 0)
        queue.failed_tasks = data.get("failed_tasks", 0)
        queue.is_paused = data.get("is_paused", False)
        queue.progress_message_id = data.get("progress_message_id")
        queue.chat_id = data.get("chat_id")
        queue.metadata = data.get("metadata", {})
        queue.progress_style = data.get("progress_style", DEFAULT_PROGRESS_STYLE)
        return queue


class ProgressManager:
    """Base class for managing user-specific queues."""

    def __init__(self):
        self.user_queues: Dict[int, UserQueue] = {}

    def get_queue(self, user_id: int) -> UserQueue:
        """Get or create a user queue."""
        if user_id not in self.user_queues:
            self.user_queues[user_id] = UserQueue(user_id=user_id)
        return self.user_queues[user_id]


class BatchCancel(Exception):
    """Exception raised when batch is cancelled"""
    pass


@dataclass
class DownloadResult:
    """Result of a download operation"""
    
    success: bool
    file_path: Optional[str] = None
    file_size: int = 0
    error: Optional[str] = None
    download_time: float = 0.0
    upload_time: float = 0.0
    
    @property
    def total_time(self) -> float:
        """Get total operation time"""
        return self.download_time + self.upload_time


@dataclass
class LinkInfo:
    """Information extracted from a Telegram link"""
    
    type: str = ""  # private, public, bot, join_chat
    source_id: Optional[str] = None
    from_id: Optional[int] = None
    to_id: Optional[int] = None
    chat_id: Optional[int] = None
    message_id: Optional[int] = None
    bot_username: Optional[str] = None
    invite_hash: Optional[str] = None
    
    @property
    def is_batch(self) -> bool:
        """Check if link is for batch download"""
        return self.from_id is not None and self.to_id is not None and self.from_id != self.to_id
    
    @property
    def batch_size(self) -> int:
        """Get batch size"""
        if self.from_id is not None and self.to_id is not None:
            return self.to_id - self.from_id + 1
        return 1