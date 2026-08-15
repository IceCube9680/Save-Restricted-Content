"""
Queue Management Service
"""

import asyncio
import time
from typing import Dict, Optional, List
from collections import defaultdict

from pyrogram import Client

from config import WAITING_TIME, MAX_BATCH_SIZE
from database.mongodb import db
from plugins.core.models import FileTask, UserQueue, ProgressManager, TaskStatus
from plugins.core.utils import get_logger
from plugins.monitoring.metrics import usage_stats

logger = get_logger(__name__)


class QueueManager(ProgressManager):
    """Enhanced queue manager with persistence and monitoring"""
    
    def __init__(self):
        super().__init__()
        self.processing_locks: Dict[int, asyncio.Lock] = {}
        self.save_tasks: Dict[int, asyncio.Task] = {}
    
    async def add_batch(self, user_id: int, tasks: List[FileTask], chat_id: int, metadata: Dict = None) -> UserQueue:
        """Add a batch of tasks to user queue"""
        queue = self.get_queue(user_id)
        
        # Ensure latest style is used
        try:
            queue.progress_style = await db.get_progress_style(user_id)
        except Exception:
            pass
            
        queue.queue = list(tasks)
        queue.current_task = None
        queue.completed_tasks = 0
        queue.failed_tasks = 0
        queue.total_tasks = len(tasks)
        queue.chat_id = chat_id
        queue.batch_start_time = time.time()
        queue.metadata = metadata or {}
        queue.is_paused = False
        
        # Update metrics
        usage_stats.increment("total_batches")
        usage_stats.add("total_tasks", len(tasks))
        
        logger.debug(f"Added batch of {len(tasks)} tasks for user {user_id}")
        
        return queue
    
    def update_task_progress(self, user_id: int, current: int, total: int, status_type: str, speed: float = 0.0) -> None:
        """Update progress of current task"""
        queue = self.get_queue(user_id)
        if queue.current_task:
            queue.current_task.update_progress(current, total)
            
            if speed > 0:
                queue.current_task.speed = speed
                if total > current:
                    queue.current_task.eta = (total - current) / speed
    
    async def start_next_task(self, user_id: int) -> Optional[FileTask]:
        """Start next task in queue with validation"""
        queue = self.get_queue(user_id)
        
        if not queue.queue or queue.current_task or queue.is_paused:
            return None
        
        # Validate batch size
        if queue.total_tasks > MAX_BATCH_SIZE:
            logger.warning(f"Batch too large for user {user_id}: {queue.total_tasks} > {MAX_BATCH_SIZE}")
            return None
        
        # Get next task
        queue.current_task = queue.queue.pop(0)
        queue.current_task.start_time = time.time()
        queue.current_task.status = TaskStatus.DOWNLOADING
        
        return queue.current_task
    
    async def complete_current_task(self, user_id: int, success: bool = True) -> Optional[FileTask]:
        """Complete current task and update stats"""
        queue = self.get_queue(user_id)
        
        if queue.current_task:
            queue.current_task.status = TaskStatus.COMPLETED if success else TaskStatus.ERROR
            queue.completed_tasks += 1
            if not success:
                queue.failed_tasks += 1
            
            completed_task = queue.current_task
            queue.current_task = None
            
            # Schedule state save
            asyncio.create_task(self._save_queue_state_after_delay(user_id))
            
            # Update metrics
            if success:
                usage_stats.increment("downloads_completed")
            else:
                usage_stats.increment("downloads_failed")
            
            return completed_task
        
        return None
    
    async def pause_queue(self, user_id: int) -> bool:
        """Pause user queue"""
        queue = self.get_queue(user_id)
        queue.is_paused = True
        if queue.current_task:
            queue.current_task.status = TaskStatus.PAUSED
        logger.info(f"Paused queue for user {user_id}")
        return True
    
    async def resume_queue(self, user_id: int) -> bool:
        """Resume user queue"""
        queue = self.get_queue(user_id)
        queue.is_paused = False
        if queue.current_task and queue.current_task.status == TaskStatus.PAUSED:
            queue.current_task.status = TaskStatus.DOWNLOADING
            queue.current_task.start_time = time.time()
        logger.info(f"Resumed queue for user {user_id}")
        return True
    
    async def cancel_queue(self, user_id: int) -> bool:
        """Cancel user queue"""
        queue = self.get_queue(user_id)
        queue.queue = []
        queue.total_tasks = queue.completed_tasks
        
        if queue.current_task:
            queue.current_task.status = TaskStatus.CANCELLED
            queue.current_task = None
        
        # Delete from database
        await db.delete_queue_state(user_id)
        
        # Cancel save task if running
        if user_id in self.save_tasks:
            self.save_tasks[user_id].cancel()
            del self.save_tasks[user_id]
        
        logger.info(f"Cancelled queue for user {user_id}")
        return True
    
    async def _save_queue_state_after_delay(self, user_id: int, delay: int = 2) -> None:
        """Save queue state after delay (debouncing)"""
        # Cancel previous save task
        if user_id in self.save_tasks:
            self.save_tasks[user_id].cancel()
        
        # Create new save task
        async def save_task():
            try:
                await asyncio.sleep(delay)
                await self._save_queue_state(user_id)
            except asyncio.CancelledError:
                pass
            finally:
                self.save_tasks.pop(user_id, None)
        
        self.save_tasks[user_id] = asyncio.create_task(save_task())

    
    async def _save_queue_state(self, user_id: int) -> None:
        """Save queue state to database"""
        queue = self.get_queue(user_id)
        
        if queue.total_tasks > 0 and queue.completed_tasks < queue.total_tasks:
            state = {
                "total_tasks": queue.total_tasks,
                "completed_tasks": queue.completed_tasks,
                "failed_tasks": queue.failed_tasks,
                "progress": queue.get_batch_progress(),
                "metadata": queue.metadata,
                "is_paused": queue.is_paused,
                "batch_start_time": getattr(queue, "batch_start_time", time.time())
            }
            
            await db.save_queue_state(user_id, state)
            logger.debug(f"Saved queue state for user {user_id}")
    
    async def get_queue_info(self, user_id: int) -> Dict:
        """Get formatted queue information"""
        queue = self.get_queue(user_id)
        
        return {
            "total": queue.total_tasks,
            "completed": queue.completed_tasks,
            "failed": queue.failed_tasks,
            "remaining": queue.get_remaining_tasks(),
            "progress": queue.get_batch_progress(),
            "eta": queue.get_batch_eta(),
            "success_rate": queue.get_success_rate(),
            "is_paused": queue.is_paused,
            "has_active_task": queue.current_task is not None
        }


# Global queue manager instance
queue_manager = QueueManager()


async def resume_all_queues(client: Client) -> int:
    """Resume all pending queues from database"""
    resumed_count = 0
    
    try:
        # Get all pending queues from database
        pending_queues = await db.get_all_pending_queues()
        
        if not pending_queues:
            logger.info("No pending queues to resume")
            return 0
        
        logger.info(f"Found {len(pending_queues)} pending queues to resume")
        
        for queue_data in pending_queues:
            try:
                user_id = queue_data["user_id"]
                metadata = queue_data.get("metadata", {})
                
                # Skip if user already has active queue
                if user_id in queue_manager.user_queues:
                    continue
                
                # Create queue
                queue = UserQueue(user_id)
                queue.metadata = metadata
                queue.completed_tasks = queue_data.get("completed_tasks", 0)
                queue.failed_tasks = queue_data.get("failed_tasks", 0)
                queue.total_tasks = queue_data.get("total_tasks", 0)
                queue.is_paused = queue_data.get("is_paused", False)
                queue.chat_id = metadata.get("chat_id")
                queue.batch_start_time = queue_data.get("batch_start_time", time.time())
                
                # Load progress style
                try:
                    queue.progress_style = await db.get_progress_style(user_id)
                except Exception:
                    pass
                
                # Calculate remaining tasks
                remaining = queue.total_tasks - queue.completed_tasks
                
                if remaining <= 0 or not queue.chat_id:
                    await db.delete_queue_state(user_id)
                    continue
                
                # Recreate tasks
                from_id = metadata.get("from_id")
                if from_id:
                    tasks = []
                    start_msgid = from_id + queue.completed_tasks
                    
                    for i in range(remaining):
                        msgid = start_msgid + i
                        task = FileTask(0, queue.chat_id, msgid, user_id)
                        tasks.append(task)
                    
                    queue.queue = tasks
                    queue_manager.user_queues[user_id] = queue
                    
                    # Send resuming message
                    try:
                        resume_msg = await client.send_message(
                            queue.chat_id,
                            "🔄 **Resuming Batch Process...**\n\n"
                            "Restoring queue state..."
                        )
                        queue.progress_message_id = resume_msg.id
                    except Exception:
                        pass
                    
                    # Set batch flag
                    from plugins.handlers.messages import batch_temp
                    batch_temp.IS_BATCH[user_id] = False
                    
                    # Start processing
                    from plugins.handlers.messages import process_batch
                    asyncio.create_task(process_batch(client, user_id))
                    
                    resumed_count += 1
                    logger.info(f"Resumed queue for user {user_id}: {remaining} tasks remaining")
                
            except Exception as e:
                logger.error(f"Failed to resume queue for user {queue_data.get('user_id')}: {e}")
        
        logger.info(f"Successfully resumed {resumed_count} queues")
        
    except Exception as e:
        logger.error(f"Error in resume_all_queues: {e}", exc_info=True)
    
    return resumed_count