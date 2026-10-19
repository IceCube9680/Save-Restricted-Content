"""
Utility functions for the bot
"""

import os
import sys
import time
import math
import json
import shutil
import asyncio
import logging
import hashlib
import random
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple, Union
from pathlib import Path

import aiofiles
import aiofiles.os

from config import (
    DOWNLOAD_TIMEOUT, UPLOAD_TIMEOUT, MAX_FILE_SIZE,
    FILE_RETENTION_TIME, DOWNLOAD_DIR
)


# ============== LOGGING SETUP ==============

def setup_logging(level: str = "INFO") -> None:
    """Setup logging configuration"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Create logs directory if not exists
    os.makedirs("logs", exist_ok=True)
    
    # File handler
    file_handler = logging.FileHandler(
        f"logs/bot_{datetime.now().strftime('%Y%m%d')}.log",
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Set specific log levels for noisy libraries
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get logger instance"""
    return logging.getLogger(name)


# ============== TIME & SIZE FORMATTING ==============

def humanbytes(size: float) -> str:
    """Convert bytes to human readable format"""
    if not size:
        return "0 B"
    
    power = 2**10
    n = 0
    dic_power_n = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB', 5: 'PB'}
    
    while size > power:
        size /= power
        n += 1
        if n >= len(dic_power_n) - 1:
            break
    
    return f"{size:.2f} {dic_power_n[n]}"


def time_formatter(seconds: float) -> str:
    """Format seconds to human readable time"""
    if seconds < 0:
        seconds = 0
    
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0:
        parts.append(f"{seconds}s")
    
    return " ".join(parts) if parts else "0s"


def format_datetime(dt: datetime, format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format datetime object"""
    return dt.strftime(format)


def get_ist_time() -> Tuple[str, str]:
    """Get current IST time (UTC+5:30)"""
    ist_time = datetime.utcnow() + timedelta(hours=5, minutes=30)
    time_str = ist_time.strftime('%I:%M:%S %p')
    date_str = ist_time.strftime('%d %b %Y')
    return time_str, date_str


# ============== FILE OPERATIONS ==============

def ensure_directory(path: str) -> str:
    """Ensure directory exists and return path"""
    os.makedirs(path, exist_ok=True)
    return path


def get_downloads_dir() -> str:
    """Get downloads directory path"""
    return ensure_directory(DOWNLOAD_DIR)


def get_temp_dir() -> str:
    """Get temporary directory path"""
    return ensure_directory("temp")


def get_thumbnails_dir() -> str:
    """Get thumbnails directory path"""
    return ensure_directory("thumbnails")


def generate_temp_filename(prefix: str = "temp", extension: str = "") -> str:
    """Generate unique temporary filename"""
    timestamp = int(time.time() * 1000)
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    filename = f"{prefix}_{timestamp}_{random_str}"
    if extension:
        if not extension.startswith("."):
            extension = f".{extension}"
        filename += extension
    return filename


async def safe_delete_file(file_path: str) -> bool:
    """Safely delete a file"""
    try:
        if file_path and os.path.exists(file_path):
            await aiofiles.os.remove(file_path)
            return True
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Failed to delete file {file_path}: {e}")
    return False


async def safe_delete_files(file_paths: List[str]) -> int:
    """Safely delete multiple files"""
    deleted = 0
    for file_path in file_paths:
        if await safe_delete_file(file_path):
            deleted += 1
    return deleted


async def get_file_size(file_path: str) -> int:
    """Get file size in bytes"""
    try:
        stat = await aiofiles.os.stat(file_path)
        return stat.st_size
    except:
        return 0


import mimetypes
from plugins.core.constants import (
    MIME_EXTENSIONS, ARCHIVE_EXTENSIONS, VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS, PHOTO_EXTENSIONS, DOCUMENT_EXTENSIONS,
    ANIMATION_EXTENSIONS, STICKER_EXTENSIONS, VOICE_EXTENSIONS
)


def get_extension_from_mime(mime_type: Optional[str], default: str = "") -> str:
    """Get proper file extension from MIME type"""
    if not mime_type:
        return default
    
    clean_mime = mime_type.lower().strip()
    
    # 1. Check known MIME extensions dictionary
    if clean_mime in MIME_EXTENSIONS:
        return MIME_EXTENSIONS[clean_mime]
    
    # 2. Check standard Python mimetypes library
    guessed = mimetypes.guess_extension(clean_mime)
    if guessed:
        if guessed == ".jpe":
            return ".jpg"
        return guessed
    
    return default


def is_archive_filename(file_name: Optional[str], mime_type: Optional[str] = "") -> bool:
    """Check if filename or MIME type corresponds to an archive"""
    if mime_type and mime_type.lower().strip() in MIME_EXTENSIONS:
        ext = MIME_EXTENSIONS[mime_type.lower().strip()]
        if ext in ARCHIVE_EXTENSIONS:
            return True
    
    if not file_name:
        return False
    
    fn_lower = file_name.lower()
    for ext in ARCHIVE_EXTENSIONS:
        if fn_lower.endswith(ext):
            return True
    return False


def truncate_text(text: str, max_length: int = 40) -> str:
    """Truncate text if too long"""
    if not text:
        return ""
    
    if len(text) > max_length:
        return text[:max_length - 3] + "..."
    return text


def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters while preserving extensions"""
    if not filename:
        return "unknown_file"
    
    # Remove path traversal and invalid characters
    invalid_chars = '<>:"/\\|?*\0\r\n\t'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    
    # Handle common double extensions (.tar.gz, .tar.bz2, .tar.xz)
    lower_fn = filename.lower()
    double_ext = None
    for dext in [".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"]:
        if lower_fn.endswith(dext):
            double_ext = dext
            break
            
    if double_ext:
        name = filename[:-len(double_ext)]
        if len(name) > 200:
            name = name[:200]
        return name + double_ext
    
    # Limit length while preserving single extension
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:245] + ext
    
    return filename


# ============== CACHE MANAGEMENT ==============

class TTLCache:
    """Time-to-live cache"""
    
    def __init__(self, ttl: int = 300):  # 5 minutes default
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.ttl = ttl
    
    def set(self, key: str, value: Any) -> None:
        """Set cache value"""
        self.cache[key] = (value, time.time() + self.ttl)
    
    def get(self, key: str) -> Optional[Any]:
        """Get cache value"""
        if key in self.cache:
            value, expiry = self.cache[key]
            if time.time() < expiry:
                return value
            else:
                del self.cache[key]
        return None
    
    def delete(self, key: str) -> None:
        """Delete cache entry"""
        if key in self.cache:
            del self.cache[key]
    
    def clear(self) -> None:
        """Clear all cache"""
        self.cache.clear()
    
    def cleanup(self) -> int:
        """Remove expired entries"""
        now = time.time()
        expired = [k for k, (_, e) in self.cache.items() if now >= e]
        for k in expired:
            del self.cache[k]
        return len(expired)


# Global cache instances
link_cache = TTLCache(ttl=600)  # 10 minutes
session_cache = TTLCache(ttl=300)  # 5 minutes
user_cache = TTLCache(ttl=60)  # 1 minute


# ============== VALIDATION ==============

def validate_telegram_link(link: str) -> bool:
    """Validate if link is a valid Telegram link"""
    import re
    
    patterns = [
        r"https://t\.me/c/(\d+)/(\d+)(?:-(\d+))?",
        r"https://t\.me/([^/]+)/(\d+)(?:-(\d+))?",
        r"https://t\.me/b/([^/]+)/(\d+)(?:-(\d+))?",
        r"https://t\.me/\+([^/]+)",
        r"https://t\.me/joinchat/([^/]+)",
    ]
    
    for pattern in patterns:
        if re.match(pattern, link):
            return True
    
    return False


def validate_file_size(file_size: int, max_size_mb: int = MAX_FILE_SIZE) -> bool:
    """Validate file size"""
    max_size_bytes = max_size_mb * 1024 * 1024
    return file_size <= max_size_bytes


# ============== RATE LIMITING ==============

class RateLimiter:
    """Rate limiter for user requests"""
    
    def __init__(self, max_requests: int = 10, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: Dict[int, List[float]] = {}
    
    def is_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to make a request"""
        now = time.time()
        
        if user_id not in self.requests:
            self.requests[user_id] = []
        
        # Clean old requests
        self.requests[user_id] = [
            t for t in self.requests[user_id]
            if now - t < self.time_window
        ]
        
        if len(self.requests[user_id]) >= self.max_requests:
            return False
        
        self.requests[user_id].append(now)
        return True
    
    def get_wait_time(self, user_id: int) -> float:
        """Get remaining wait time for user"""
        if user_id not in self.requests:
            return 0
        
        now = time.time()
        recent_requests = [
            t for t in self.requests[user_id]
            if now - t < self.time_window
        ]
        
        if len(recent_requests) < self.max_requests:
            return 0
        
        oldest_request = min(recent_requests)
        return self.time_window - (now - oldest_request)
    
    def reset(self, user_id: int) -> None:
        """Reset rate limit for user"""
        if user_id in self.requests:
            del self.requests[user_id]


# Global rate limiter
rate_limiter = RateLimiter()


# ============== JSON HELPERS ==============

def json_serializer(obj: Any) -> str:
    """JSON serializer with datetime support"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def json_deserializer(data: str) -> Any:
    """JSON deserializer with datetime support"""
    return json.loads(data)


# ============== DECORATORS ==============

def retry_on_error(max_retries: int = 3, delay: float = 1.0):
    """Retry decorator for async functions"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        await asyncio.sleep(delay * (2 ** attempt))
            if last_error is not None:
                raise last_error
            raise RuntimeError(f"Retry failed with max_retries={max_retries}")
        return wrapper
    return decorator


def measure_time(func):
    """Measure execution time decorator"""
    async def wrapper(*args, **kwargs):
        start = time.time()
        result = await func(*args, **kwargs)
        elapsed = time.time() - start
        logger = get_logger(__name__)
        logger.debug(f"{func.__name__} took {elapsed:.2f}s")
        return result
    return wrapper