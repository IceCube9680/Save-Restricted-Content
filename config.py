"""
Configuration Management - MongoDB Version
"""

import os
from typing import List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def get_bool_env(key: str, default: bool = False) -> bool:
    """Get boolean from environment variable"""
    val = os.getenv(key, str(default)).lower()
    return val in ('true', '1', 't', 'y', 'yes')


def get_int_env(key: str, default: int = 0) -> int:
    """Get integer from environment variable"""
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


def get_float_env(key: str, default: float = 0.0) -> float:
    """Get float from environment variable"""
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


def get_list_env(key: str, default: str = "") -> List[int]:
    """Get list of integers from environment variable"""
    val = os.getenv(key, default)
    if not val:
        return []
    try:
        return [int(x.strip()) for x in val.split(",") if x.strip()]
    except (ValueError, TypeError):
        return []


# ============== TELEGRAM API CONFIGURATION ==============
API_ID = get_int_env("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Validate required settings
if not API_ID or not API_HASH or not BOT_TOKEN:
    raise ValueError("API_ID, API_HASH, and BOT_TOKEN must be set in .env file")

# ============== MONGODB CONFIGURATION ==============
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "save_restricted_bot")
MONGODB_POOL_SIZE = get_int_env("MONGODB_POOL_SIZE", 50)
MONGODB_MIN_POOL_SIZE = get_int_env("MONGODB_MIN_POOL_SIZE", 10)
MONGODB_MAX_IDLE_TIME = get_int_env("MONGODB_MAX_IDLE_TIME", 30000)  # ms

# ============== BOT CONFIGURATION ==============
ADMINS = get_list_env("ADMINS", "")
LOG_CHANNEL = os.getenv("LOG_CHANNEL")
ERROR_MESSAGE = get_bool_env("ERROR_MESSAGE", True)

# ============== DOWNLOAD CONFIGURATION ==============
WAITING_TIME = get_int_env("WAITING_TIME", 2)
MAX_FILE_SIZE = get_int_env("MAX_FILE_SIZE", 2000)  # MB
MAX_BATCH_SIZE = get_int_env("MAX_BATCH_SIZE", 1000000000000)
DOWNLOAD_TIMEOUT = get_int_env("DOWNLOAD_TIMEOUT", 300)  # seconds
UPLOAD_TIMEOUT = get_int_env("UPLOAD_TIMEOUT", 300)  # seconds
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")

# ============== SESSION CONFIGURATION ==============
LOGIN_SYSTEM = get_bool_env("LOGIN_SYSTEM", False)
STRING_SESSION = os.getenv("STRING_SESSION")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")  # For session encryption

# ============== CHANNEL CONFIGURATION ==============
CHANNEL_ID = os.getenv("CHANNEL_ID")
ENABLE_GLOBAL_CHANNEL = get_bool_env("ENABLE_GLOBAL_CHANNEL", False)
GLOBAL_CHANNEL_ID = os.getenv("GLOBAL_CHANNEL_ID")

# ============== RATE LIMITING ==============
RATE_LIMIT_ENABLED = get_bool_env("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_REQUESTS = get_int_env("RATE_LIMIT_REQUESTS", 10)  # requests
RATE_LIMIT_WINDOW = get_int_env("RATE_LIMIT_WINDOW", 60)  # seconds

# ============== PROGRESS ANIMATIONS ==============
ANIMATION_SPEED = get_float_env("ANIMATION_SPEED", 1.0)
DEFAULT_PROGRESS_STYLE = os.getenv("DEFAULT_PROGRESS_STYLE", "modern")
ENABLE_WAVE_ANIMATION = get_bool_env("ENABLE_WAVE_ANIMATION", True)

# ============== CLEANUP CONFIGURATION ==============
CLEANUP_INTERVAL = get_int_env("CLEANUP_INTERVAL", 3600)  # 1 hour
FILE_RETENTION_TIME = get_int_env("FILE_RETENTION_TIME", 3600)  # 1 hour
MAX_DOWNLOAD_DIR_SIZE = get_int_env("MAX_DOWNLOAD_DIR_SIZE", 1024)  # MB

# ============== BACKUP CONFIGURATION ==============
BACKUP_ENABLED = get_bool_env("BACKUP_ENABLED", True)
BACKUP_INTERVAL = get_int_env("BACKUP_INTERVAL", 86400)  # 24 hours
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
MAX_BACKUP_COUNT = get_int_env("MAX_BACKUP_COUNT", 10)

# ============== PERFORMANCE ==============
WORKERS = get_int_env("WORKERS", 20)
MAX_CONCURRENT_TRANSMISSIONS = get_int_env("MAX_CONCURRENT_TRANSMISSIONS", 10)
SLEEP_THRESHOLD = get_int_env("SLEEP_THRESHOLD", 10)
UPDATE_INTERVAL = get_float_env("UPDATE_INTERVAL", 2.0)


# ============== FEATURE TOGGLES ==============
ENABLE_STATISTICS = get_bool_env("ENABLE_STATISTICS", True)
ENABLE_HEALTH_MONITORING = get_bool_env("ENABLE_HEALTH_MONITORING", True)
ENABLE_AUTO_RESTART = get_bool_env("ENABLE_AUTO_RESTART", True)
ENABLE_FILE_FILTERS = get_bool_env("ENABLE_FILE_FILTERS", True)

# ============== VALIDATION ==============
def validate_config():
    """Validate critical configuration"""
    errors = []
    
    if LOGIN_SYSTEM and not ENCRYPTION_KEY:
        errors.append("ENCRYPTION_KEY is required when LOGIN_SYSTEM is enabled")
    
    if ENABLE_GLOBAL_CHANNEL and not GLOBAL_CHANNEL_ID:
        errors.append("GLOBAL_CHANNEL_ID is required when ENABLE_GLOBAL_CHANNEL is enabled")
    
    # MongoDB URI validation
    if not MONGODB_URI:
        errors.append("MONGODB_URI is required")
    
    if errors:
        raise ValueError("\n".join(errors))


# Validate on import
validate_config()