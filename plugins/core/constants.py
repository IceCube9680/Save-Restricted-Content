"""
Constants and enums used throughout the bot
"""

from enum import Enum, auto
from typing import Dict, List


class TaskStatus(Enum):
    """Task status enum"""
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    FORWARDING = "forwarding"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    ERROR = "error"
    SKIPPED = "skipped"


class FileType(Enum):
    """File type enum"""
    DOCUMENT = "document"
    VIDEO = "video"
    VIDEO_NOTE = "videonote"
    AUDIO = "audio"
    PHOTO = "photo"
    ANIMATION = "animation"
    STICKER = "sticker"
    VOICE = "voice"
    ARCHIVE = "archive"
    TEXT = "text"
    POLL = "poll"
    LOCATION = "location"
    VENUE = "venue"
    CONTACT = "contact"
    DICE = "dice"
    UNKNOWN = "unknown"


class MessageType(Enum):
    """Message type enum"""
    PRIVATE = "private"
    PUBLIC = "public"
    BOT = "bot"
    JOIN_CHAT = "join_chat"


# Extension sets for categorization and filtering
ARCHIVE_EXTENSIONS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
    ".tgz", ".zst", ".cab", ".dmg", ".apk", ".xapk", ".apkm",
    ".deb", ".rpm", ".jar", ".war", ".ear", ".tar.gz", ".tar.bz2",
    ".tar.xz", ".lzma", ".lzo", ".lz", ".z", ".cpio", ".gtar", ".a"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".wmv",
    ".3gp", ".3g2", ".m4v", ".ts", ".mpg", ".mpeg", ".m2ts",
    ".vob", ".ogv", ".rmvb", ".rm", ".asf", ".divx", ".f4v",
    ".m2v", ".mpe", ".mpv", ".mts", ".wtv"
}

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg", ".opus",
    ".wma", ".alac", ".aiff", ".aif", ".amr", ".mid", ".midi",
    ".ape", ".ac3", ".dts", ".mka", ".ra", ".voc", ".m4b",
    ".m4p", ".m4r", ".wv", ".spx"
}

PHOTO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff",
    ".tif", ".heic", ".heif", ".svg", ".ico", ".avif", ".psd",
    ".ai", ".raw", ".cr2", ".nef", ".dng", ".eps", ".cdr", ".jfif"
}

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".epub", ".mobi", ".azw3", ".djvu", ".csv", ".json",
    ".xml", ".html", ".htm", ".css", ".js", ".ts", ".py", ".c",
    ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".sh", ".bat",
    ".ps1", ".sql", ".srt", ".vtt", ".ass", ".sub", ".exe", ".msi",
    ".torrent", ".bin", ".dat", ".log", ".md", ".rtf", ".odt",
    ".ods", ".odp", ".tex", ".yaml", ".yml", ".ini", ".conf", ".cfg"
}

ANIMATION_EXTENSIONS = {".gif", ".mp4", ".webm"}
STICKER_EXTENSIONS = {".webp", ".tgs", ".webm"}
VOICE_EXTENSIONS = {".ogg", ".oga", ".opus"}

# Default file extensions mapping by FileType
FILE_EXTENSIONS: Dict[FileType, str] = {
    FileType.PHOTO: ".jpg",
    FileType.VIDEO: ".mp4",
    FileType.VIDEO_NOTE: ".mp4",
    FileType.AUDIO: ".mp3",
    FileType.ANIMATION: ".mp4",
    FileType.VOICE: ".ogg",
    FileType.STICKER: ".webp",
    FileType.ARCHIVE: ".zip",
    FileType.TEXT: ".txt",
    FileType.DOCUMENT: ".bin",
    FileType.POLL: ".txt",
    FileType.LOCATION: ".txt",
    FileType.VENUE: ".txt",
    FileType.CONTACT: ".vcf",
    FileType.DICE: ".txt",
    FileType.UNKNOWN: ".bin",
}

# Comprehensive file extensions based on MIME type
MIME_EXTENSIONS: Dict[str, str] = {
    # Video MIME types
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-flv": ".flv",
    "video/x-ms-wmv": ".wmv",
    "video/3gpp": ".3gp",
    "video/3gpp2": ".3g2",
    "video/x-m4v": ".m4v",
    "video/mp2t": ".ts",
    "video/mpeg": ".mpeg",
    "video/ogg": ".ogv",
    "video/vnd.dlna.mpeg-tts": ".m2ts",
    "video/x-ms-asf": ".asf",
    "video/divx": ".divx",
    
    # Audio MIME types
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/aac": ".aac",
    "audio/x-aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/x-ms-wma": ".wma",
    "audio/alac": ".alac",
    "audio/x-alac": ".alac",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
    "audio/amr": ".amr",
    "audio/midi": ".mid",
    "audio/x-midi": ".mid",
    "audio/x-ape": ".ape",
    "audio/ac3": ".ac3",
    "audio/x-matroska": ".mka",
    "audio/oga": ".oga",
    
    # Image / Photo MIME types
    "image/jpeg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/x-bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/avif": ".avif",
    "image/vnd.adobe.photoshop": ".psd",
    
    # Sticker MIME types
    "application/x-tgsticker": ".tgs",
    "application/x-tgs": ".tgs",
    
    # Archive MIME types
    "application/zip": ".zip",
    "application/x-zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar",
    "application/x-rar": ".rar",
    "application/vnd.rar": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/x-tar": ".tar",
    "application/gzip": ".gz",
    "application/x-gzip": ".gz",
    "application/x-bzip2": ".bz2",
    "application/x-bzip": ".bz2",
    "application/x-xz": ".xz",
    "application/x-iso9660-image": ".iso",
    "application/x-compressed-tar": ".tgz",
    "application/zstd": ".zst",
    "application/x-archive": ".a",
    "application/vnd.android.package-archive": ".apk",
    "application/x-apple-diskimage": ".dmg",
    "application/x-gtar": ".gtar",
    "application/x-cpio": ".cpio",
    "application/java-archive": ".jar",
    
    # Document & Code MIME types
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "text/html": ".html",
    "text/css": ".css",
    "text/javascript": ".js",
    "application/javascript": ".js",
    "text/x-python": ".py",
    "application/x-python-code": ".py",
    "application/epub+zip": ".epub",
    "application/x-mobipocket-ebook": ".mobi",
    "application/x-msdownload": ".exe",
    "application/x-bittorrent": ".torrent",
    "text/vtt": ".vtt",
    "application/x-subrip": ".srt",
    "text/x-ssa": ".ass",
    "application/octet-stream": ".bin",
    "text/x-vcard": ".vcf",
}

# Allowed file types for download
ALLOWED_FILE_TYPES: List[str] = [
    "document", "video", "videonote", "audio", "photo",
    "animation", "sticker", "voice", "archive", "text",
    "poll", "location", "venue", "contact", "dice"
]

# Maximum file size per type (in MB)
MAX_FILE_SIZE_BY_TYPE: Dict[str, int] = {
    "document": 2000,
    "archive": 2000,
    "video": 2000,
    "videonote": 2000,
    "audio": 2000,
    "photo": 50,
    "animation": 100,
    "sticker": 20,
    "voice": 100,
}

# Progress bar styles
PROGRESS_STYLES: List[str] = ["gradient", "block", "circle", "square", "arrow", "modern"]

# Spinner frames for animations
SPINNER_FRAMES: Dict[str, List[str]] = {
    "downloading": ["⡀", "⡄", "⡆", "⡇", "⡏", "⡟", "⡿", "⣿", "⣷", "⣯", "⣟", "⣿"],
    "uploading": ["⬆️", "↗️", "➡️", "↘️", "⬇️", "↙️", "⬅️", "↖️"],
    "forwarding": ["⏩", "▶️", "➡️", "↗️", "🔄"],
    "processing": ["⚙️", "🔧", "🛠️", "🔨"],
    "waiting": ["⏳", "⌛", "⏰", "🕐", "🕑", "🕒", "🕓", "🕔"],
    "success": ["✅", "✨", "🎉", "🔥", "🌟", "💫"],
    "error": ["❌", "⚠️", "🚫", "💥"],
}

# Progress bar characters
PROGRESS_BAR_CHARS: Dict[str, List[str]] = {
    "gradient": ["░", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"],
    "block": ["□", "░", "▒", "▓", "█"],
    "circle": ["○", "◔", "◑", "◕", "●"],
    "square": ["□", "◱", "◲", "■"],
    "arrow": ["-", "▸", "▹", "►", "▻", "▶"],
    "modern": ["⣀", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"],
}

# File icons
FILE_ICONS: Dict[str, str] = {
    "video": "🎬",
    "videonote": "📹",
    "video_note": "📹",
    "audio": "🎵",
    "document": "📄",
    "photo": "🖼️",
    "animation": "🎞️",
    "sticker": "🏷️",
    "voice": "🎤",
    "text": "📝",
    "zip": "📦",
    "archive": "🗜️",
    "executable": "⚙️",
    "code": "💻",
    "pdf": "📕",
    "poll": "📊",
    "location": "📍",
    "venue": "🏢",
    "contact": "👤",
    "dice": "🎲",
    "unknown": "📁",
}

# Status emojis
STATUS_EMOJIS: Dict[str, str] = {
    "downloading": "⬇️",
    "uploading": "⬆️",
    "forwarding": "⏩",
    "processing": "⚙️",
    "queued": "⏳",
    "completed": "✅",
    "paused": "⏸️",
    "cancelled": "🚫",
    "error": "❌",
    "skipped": "⏭️",
}

# Speed emojis
SPEED_EMOJIS: Dict[str, str] = {
    "very_fast": "🚀",  # >10 MB/s
    "fast": "⚡",        # >1 MB/s
    "medium": "🐇",      # >100 KB/s
    "slow": "🚶",        # >10 KB/s
    "very_slow": "🐌",   # <10 KB/s
}

# Command descriptions for help menu
COMMAND_DESCRIPTIONS: Dict[str, str] = {
    "start": "Start the bot",
    "help": "Show help message",
    "login": "Login with phone number (for private content)",
    "logout": "Logout and clear session",
    "settings": "Configure bot settings",
    "cancel": "Cancel current operation",
    "stats": "Show bot statistics (admin only)",
    "users": "Show user list (admin only)",
    "backup": "Create database backup (admin only)",
    "broadcast": "Send message to all users (admin only)",
    "restart": "Restart the bot (admin only)",
}

# Error messages
ERROR_MESSAGES: Dict[str, str] = {
    "access_denied": "⛔ **Access Denied**\n\nYou are not authorized to use this bot.",
    "login_required": "🔐 **Login Required**\n\nFor downloading restricted content you have to /login first.",
    "session_expired": "❌ **Session Expired**\n\nYour login session has expired. Please /logout first then /login again.",
    "invalid_link": "❌ **Invalid Link Format**\n\nPlease provide a valid Telegram link.",
    "batch_active": "⏳ **One task is already processing. Please wait for it to complete.**\n\nUse /cancel to stop current operation.",
    "no_session": "❌ **String Session is not Set**\n\nPlease configure a string session in the bot settings.",
}

# Success messages
SUCCESS_MESSAGES: Dict[str, str] = {
    "chat_joined": "✅ Chat Joined",
    "already_joined": "✅ Chat already Joined",
    "cancelled": "✅ **Batch Successfully Cancelled.**\n\nAll operations have been stopped and resources freed.",
}