"""
Health Monitoring System
"""

import asyncio
import time
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from pyrogram import Client
from pyrogram.errors import FloodWait

from config import ENABLE_HEALTH_MONITORING, ENABLE_AUTO_RESTART, ADMINS, LOG_CHANNEL
from database.mongodb import db
from plugins.core.utils import get_logger
from plugins.services.queue_manager import queue_manager
from plugins.monitoring.metrics import usage_stats

logger = get_logger(__name__)


@dataclass
class HealthStatus:
    """Health check status"""
    healthy: bool = True
    message: str = "All systems operational"
    timestamp: float = field(default_factory=time.time)
    checks: Dict[str, bool] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)


class HealthMonitor:
    """Monitor bot health and performance"""
    
    def __init__(self, bot: Client):
        self.bot = bot
        self.status = HealthStatus()
        self.is_running = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.failure_count = 0
        self.max_failures = 5
        self.check_interval = 60  # 1 minute
        self.last_restart = 0
        self.restart_cooldown = 300  # 5 minutes
    
    async def start_monitoring(self) -> None:
        """Start the health monitoring loop"""
        if not ENABLE_HEALTH_MONITORING:
            logger.info("Health monitoring is disabled")
            return
        
        self.is_running = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitoring started")
    
    async def stop_monitoring(self) -> None:
        """Stop health monitoring"""
        self.is_running = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitoring stopped")
    
    async def _monitor_loop(self) -> None:
        """Main monitoring loop"""
        while self.is_running:
            try:
                await self.run_health_check()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor loop: {e}")
                await asyncio.sleep(10)
    
    async def run_health_check(self) -> HealthStatus:
        """Run comprehensive health check"""
        checks = {}
        metrics = {}
        
        # Check bot connection
        checks["bot_connection"] = await self._check_bot_connection()
        
        # Check database connection
        checks["database"] = await self._check_database()
        
        # Check memory usage
        checks["memory"] = self._check_memory_usage()
        
        # Check disk space
        checks["disk_space"] = self._check_disk_space()
        
        # Check queue health
        checks["queues"] = await self._check_queues()
        
        # Update status
        all_healthy = all(checks.values())
        self.status = HealthStatus(
            healthy=all_healthy,
            message="All systems operational" if all_healthy else "Some systems degraded",
            timestamp=time.time(),
            checks=checks,
            metrics=metrics
        )
        
        # Handle failures
        if not all_healthy:
            self.failure_count += 1
            logger.warning(f"Health check failed ({self.failure_count}/{self.max_failures}): {checks}")
            
            if self.failure_count >= self.max_failures:
                await self._handle_critical_failure()
        else:
            # Reset failure count on success
            self.failure_count = 0
        
        return self.status
    
    async def _check_bot_connection(self) -> bool:
        """Check bot connection to Telegram"""
        try:
            me = await self.bot.get_me()
            return me is not None
        except FloodWait as e:
            logger.warning(f"Flood wait during health check: {e.value}s")
            return True  # Still considered healthy, just rate limited
        except Exception as e:
            logger.error(f"Bot connection check failed: {e}")
            return False
    
    async def _check_database(self) -> bool:
        """Check database connection"""
        try:
            # Simple query to test connection
            await db.total_users_count()
            return True
        except Exception as e:
            logger.error(f"Database check failed: {e}")
            return False
    
    def _check_memory_usage(self) -> bool:
        """Check memory usage"""
        try:
            import psutil
            process = psutil.Process(os.getpid())
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            # Warn if memory usage > 500MB
            if memory_mb > 500:
                logger.warning(f"High memory usage: {memory_mb:.1f}MB")
            
            # Critical if memory usage > 1GB
            return memory_mb <= 1000
            
        except ImportError:
            # psutil not available
            return True
        except Exception as e:
            logger.error(f"Memory check failed: {e}")
            return True
    
    def _check_disk_space(self) -> bool:
        """Check available disk space"""
        try:
            import shutil
            usage = shutil.disk_usage(".")
            free_gb = usage.free / 1024 / 1024 / 1024
            
            # Warn if free space < 1GB
            if free_gb < 1:
                logger.warning(f"Low disk space: {free_gb:.1f}GB free")
            
            # Critical if free space < 100MB
            return free_gb >= 0.1
            
        except Exception as e:
            logger.error(f"Disk space check failed: {e}")
            return True
    
    async def _check_queues(self) -> bool:
        """Check queue health"""
        try:
            # Check for stuck queues
            for user_id, queue in queue_manager.user_queues.items():
                if queue.current_task:
                    # Check if task has been processing for too long (> 10 minutes)
                    if queue.current_task.start_time:
                        elapsed = time.time() - queue.current_task.start_time
                        if elapsed > 600:  # 10 minutes
                            logger.warning(f"Task stuck for user {user_id}: {elapsed:.0f}s")
                            # Don't mark as unhealthy, just warn
            
            return True
            
        except Exception as e:
            logger.error(f"Queue health check failed: {e}")
            return True
    
    async def _handle_critical_failure(self) -> None:
        """Handle critical system failures"""
        logger.critical(f"Critical health failure: {self.status.checks}")
        
        # Notify admins
        await self._notify_admins()
        
        # Attempt auto-restart if enabled
        if ENABLE_AUTO_RESTART:
            await self._attempt_restart()
    
    async def _notify_admins(self) -> None:
        """Notify admins about critical failure"""
        if not ADMINS:
            return
        
        message = (
            f"🚨 **CRITICAL HEALTH ALERT**\n\n"
            f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**Status:** System degraded\n\n"
            f"**Checks:**\n"
        )
        
        for check, passed in self.status.checks.items():
            status = "✅" if passed else "❌"
            message += f"{status} {check}\n"
        
        message += f"\n**Failures:** {self.failure_count}/{self.max_failures}"
        
        for admin_id in ADMINS:
            try:
                await self.bot.send_message(admin_id, message)
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        
        if LOG_CHANNEL:
            try:
                await self.bot.send_message(int(LOG_CHANNEL), message)
            except:
                pass
    
    async def _attempt_restart(self) -> None:
        """Attempt to restart the bot"""
        now = time.time()
        
        # Check restart cooldown
        if now - self.last_restart < self.restart_cooldown:
            logger.warning(f"Restart cooldown active, skipping")
            return
        
        logger.critical("Attempting auto-restart...")
        self.last_restart = now
        
        try:
            # Notify about restart
            for admin_id in ADMINS:
                try:
                    await self.bot.send_message(
                        admin_id,
                        "🔄 **Auto-restart initiated due to health check failures**"
                    )
                except:
                    pass
            
            # Perform restart
            python = sys.executable
            os.execl(python, python, *sys.argv)
            
        except Exception as e:
            logger.error(f"Auto-restart failed: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current health status"""
        return {
            "healthy": self.status.healthy,
            "message": self.status.message,
            "timestamp": self.status.timestamp,
            "checks": self.status.checks,
            "uptime": usage_stats.get_summary()["uptime"],
            "failure_count": self.failure_count,
        }