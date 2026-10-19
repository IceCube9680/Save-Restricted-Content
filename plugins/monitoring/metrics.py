"""
Usage Statistics and Metrics
"""

import time
from typing import Dict, Any
from collections import defaultdict
from datetime import datetime, timedelta

from database.mongodb import db
from plugins.core.utils import get_logger

logger = get_logger(__name__)


class UsageStatistics:
    """Track usage statistics and metrics"""
    
    def __init__(self):
        # Runtime metrics (reset on restart)
        self.runtime_stats = {
            "total_downloads": 0,
            "total_uploads": 0,
            "downloads_completed": 0,
            "downloads_failed": 0,
            "total_batches": 0,
            "total_tasks": 0,
            "total_errors": 0,
            "active_sessions": 0,
            "total_bandwidth": 0,
            "start_time": time.time()
        }
        
        # Rate tracking
        self.rates = defaultdict(lambda: {"count": 0, "start": time.time()})
        
        # Cache for database stats
        self.cached_stats = {}
        self.cache_ttl = 300  # 5 minutes
        self.last_cache_update = 0
    
    def increment(self, key: str, value: int = 1) -> None:
        """Increment a runtime statistic"""
        if key in self.runtime_stats:
            self.runtime_stats[key] += value
        
        # Track rate
        if key in ["total_downloads", "total_uploads", "total_errors"]:
            now = time.time()
            rate_key = f"{key}_rate"
            self.rates[rate_key]["count"] += value
            
            # Reset rate every minute
            if now - self.rates[rate_key]["start"] >= 60:
                self.rates[rate_key]["count"] = value
                self.rates[rate_key]["start"] = now
    
    def decrement(self, key: str, value: int = 1) -> None:
        """Decrement a runtime statistic"""
        if key in self.runtime_stats:
            self.runtime_stats[key] = max(0, self.runtime_stats[key] - value)
    
    def add(self, key: str, value: int) -> None:
        """Add value to a statistic"""
        if key in self.runtime_stats:
            self.runtime_stats[key] += value
    
    def set(self, key: str, value: int) -> None:
        """Set a statistic to a specific value"""
        if key in self.runtime_stats:
            self.runtime_stats[key] = value
    
    def get_rate(self, key: str) -> float:
        """Get rate per minute for a statistic"""
        rate_key = f"{key}_rate"
        if rate_key in self.rates:
            elapsed = time.time() - self.rates[rate_key]["start"]
            if elapsed > 0:
                return (self.rates[rate_key]["count"] / elapsed) * 60
        return 0
    
    async def get_database_stats(self) -> Dict[str, Any]:
        """Get statistics from database"""
        now = time.time()
        
        # Check cache
        if now - self.last_cache_update < self.cache_ttl:
            return self.cached_stats
        
        try:
            stats = {}
            
            # Get user counts
            stats["total_users"] = await db.total_users_count()
            stats["active_users"] = await db.get_active_users_count(30)
            
            # Get session counts
            active_sessions = await db.get_active_sessions()
            stats["active_sessions"] = len(active_sessions)
            
            # Get global stats
            global_stats = await db.get_global_stats()
            stats["global_downloads"] = global_stats["total_downloads"]
            stats["global_uploads"] = global_stats["total_uploads"]
            stats["global_bandwidth"] = global_stats["total_bandwidth"]
            
            # Get download stats from history
            # This would require additional DB queries
            
            # Update cache
            self.cached_stats = stats
            self.last_cache_update = now
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting database stats: {e}")
            return {}
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive statistics summary"""
        uptime = time.time() - self.runtime_stats["start_time"]
        
        # Calculate success rate
        total_completed = self.runtime_stats.get("downloads_completed", 0)
        total_failed = self.runtime_stats.get("downloads_failed", 0)
        total_downloads = total_completed + total_failed
        success_rate = (total_completed / total_downloads * 100) if total_downloads > 0 else 100.0
        
        # Format bandwidth
        bandwidth = self.runtime_stats.get("total_bandwidth", 0)
        bandwidth_str = self._format_bytes(bandwidth)
        
        # Calculate rates
        download_rate = self.get_rate("total_downloads")
        upload_rate = self.get_rate("total_uploads")
        
        return {
            # Runtime stats
            "uptime": self._format_time(uptime),
            "total_downloads": self.runtime_stats.get("total_downloads", 0),
            "total_uploads": self.runtime_stats.get("total_uploads", 0),
            "downloads_completed": total_completed,
            "downloads_failed": total_failed,
            "total_batches": self.runtime_stats.get("total_batches", 0),
            "total_tasks": self.runtime_stats.get("total_tasks", 0),
            "total_errors": self.runtime_stats.get("total_errors", 0),
            "active_sessions": self.runtime_stats.get("active_sessions", 0),
            "total_bandwidth": bandwidth_str,
            "success_rate": f"{success_rate:.1f}%",
            
            # Rates
            "download_rate": f"{download_rate:.1f}/min",
            "upload_rate": f"{upload_rate:.1f}/min",
            
            # Performance
            "avg_download_time": self.runtime_stats.get("avg_download_time", 0),
            "avg_upload_time": self.runtime_stats.get("avg_upload_time", 0),
        }
    
    def _format_time(self, seconds: float) -> str:
        """Format time in human readable format"""
        days, seconds = divmod(int(seconds), 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        
        return " ".join(parts)
    
    def _format_bytes(self, bytes_value: int) -> str:
        """Format bytes in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_value < 1024.0:
                return f"{bytes_value:.2f} {unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.2f} PB"
    
    def reset(self) -> None:
        """Reset all runtime statistics"""
        self.runtime_stats = {
            "total_downloads": 0,
            "total_uploads": 0,
            "downloads_completed": 0,
            "downloads_failed": 0,
            "total_batches": 0,
            "total_tasks": 0,
            "total_errors": 0,
            "active_sessions": 0,
            "total_bandwidth": 0,
            "start_time": time.time()
        }
        self.rates.clear()
        logger.info("Statistics reset")


# Global usage statistics instance
usage_stats = UsageStatistics()