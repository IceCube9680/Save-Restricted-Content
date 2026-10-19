"""
MongoDB Database Handler for Save Restricted Content Bot
Pure MongoDB implementation without SQLAlchemy
"""

import os
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Union


import motor.motor_asyncio
from pymongo import IndexModel, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, ConnectionFailure

from config import MONGODB_URI, MONGODB_DB_NAME
from plugins.core.utils import get_logger

logger = get_logger(__name__)


class MongoDB:
    """MongoDB Database Manager - Pure MongoDB implementation"""

    def __init__(self):
        self.client = None
        self.db = None
        self.is_connected = False

    async def connect(self):
        """Establish connection to MongoDB"""
        try:
            # Create client with connection pool
            self.client = motor.motor_asyncio.AsyncIOMotorClient(
                MONGODB_URI,
                maxPoolSize=50,
                minPoolSize=10,
                maxIdleTimeMS=30000,
                connectTimeoutMS=5000,
                socketTimeoutMS=30000,
                serverSelectionTimeoutMS=5000,
                retryWrites=True,
                retryReads=True
            )

            # Test connection
            await self.client.admin.command('ping')

            # Get database
            self.db = self.client[MONGODB_DB_NAME]

            # Create indexes
            await self._create_indexes()

            self.is_connected = True
            logger.info(f"✅ Connected to MongoDB: {MONGODB_DB_NAME}")

        except ConnectionFailure as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ MongoDB error: {e}")
            raise

    async def disconnect(self):
        """Close database connection"""
        if self.client:
            self.client.close()
            self.is_connected = False
            logger.info("🔌 Disconnected from MongoDB")

    async def _create_indexes(self):
        """Create all database indexes"""

        # Users collection indexes
        await self.db.users.create_index("user_id", unique=True)
        await self.db.users.create_index("username", sparse=True)
        await self.db.users.create_index([("created_at", DESCENDING)])
        await self.db.users.create_index([("last_active", DESCENDING)])
        await self.db.users.create_index("is_banned")

        # Sessions collection indexes
        await self.db.sessions.create_index("user_id", unique=True)
        await self.db.sessions.create_index([("expires_at", ASCENDING)])
        await self.db.sessions.create_index([("last_used", DESCENDING)])

        # Preferences collection indexes
        await self.db.preferences.create_index("user_id", unique=True)

        # Queues collection indexes
        await self.db.queues.create_index("user_id", unique=True)
        await self.db.queues.create_index([("updated_at", DESCENDING)])

        # History collection indexes
        await self.db.history.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        await self.db.history.create_index([("created_at", DESCENDING)])
        await self.db.history.create_index("file_type")
        await self.db.history.create_index("success")

        # Statistics collection indexes
        await self.db.statistics.create_index([("metric_name", ASCENDING), ("date", DESCENDING)], unique=True)

        # Backup collection indexes
        await self.db.backups.create_index([("created_at", DESCENDING)])

        # Settings collection indexes
        await self.db.settings.create_index("key", unique=True)

        logger.info("✅ MongoDB indexes created successfully")

    # ============== USER METHODS ==============

    async def add_user(self, user_id: int, first_name: str, username: str = None,
                      last_name: str = None, phone: str = None) -> bool:
        """Add or update user in database"""
        try:
            now = datetime.now(timezone.utc)

            user_data = {
                "user_id": user_id,
                "first_name": first_name,
                "last_name": last_name,
                "username": username,
                "phone_number": phone,
                "language": "en",
                "is_active": True,
                "is_banned": False,
                "is_admin": False,
                "created_at": now,
                "last_active": now,
                "total_downloads": 0,
                "total_uploads": 0,
                "total_bandwidth": 0,
                "updated_at": now
            }

            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": user_data},
                upsert=True
            )

            logger.debug(f"User {user_id} added/updated in database")
            return True

        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")
            return False

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        try:
            user = await self.db.users.find_one({"user_id": user_id}, {"_id": 0})
            return user
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            return None

    async def get_all_users(self, limit: int = 1000, skip: int = 0) -> List[Dict[str, Any]]:
        """Get all users with pagination"""
        try:
            cursor = self.db.users.find({}, {"_id": 0}).sort("created_at", DESCENDING).skip(skip).limit(limit)
            users = await cursor.to_list(length=limit)
            return users
        except Exception as e:
            logger.error(f"Error getting all users: {e}")
            return []

    async def total_users_count(self) -> int:
        """Get total number of users"""
        try:
            return await self.db.users.count_documents({})
        except Exception as e:
            logger.error(f"Error counting users: {e}")
            return 0

    async def is_user_exist(self, user_id: int) -> bool:
        """Check if user exists"""
        try:
            count = await self.db.users.count_documents({"user_id": user_id})
            return count > 0
        except Exception as e:
            logger.error(f"Error checking user existence {user_id}: {e}")
            return False

    async def get_active_users_count(self, minutes: int = 30) -> int:
        """Get users active in last N minutes"""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            return await self.db.users.count_documents({
                "last_active": {"$gte": cutoff}
            })
        except Exception as e:
            logger.error(f"Error counting active users: {e}")
            return 0

    async def update_user_activity(self, user_id: int) -> None:
        """Update user's last active timestamp"""
        try:
            await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"last_active": datetime.now(timezone.utc)}}
            )
        except Exception as e:
            logger.error(f"Error updating user activity {user_id}: {e}")

    async def increment_user_stats(self, user_id: int, downloads: int = 0,
                                  uploads: int = 0, bandwidth: int = 0) -> None:
        """Increment user statistics"""
        try:
            await self.db.users.update_one(
                {"user_id": user_id},
                {
                    "$inc": {
                        "total_downloads": downloads,
                        "total_uploads": uploads,
                        "total_bandwidth": bandwidth
                    },
                    "$set": {"updated_at": datetime.now(timezone.utc)}
                }
            )
        except Exception as e:
            logger.error(f"Error incrementing user stats {user_id}: {e}")

    async def get_global_stats(self) -> Dict[str, int]:
        """Get global statistics by aggregating user stats"""
        try:
            pipeline = [
                {"$group": {
                    "_id": None,
                    "total_downloads": {"$sum": "$total_downloads"},
                    "total_uploads": {"$sum": "$total_uploads"},
                    "total_bandwidth": {"$sum": "$total_bandwidth"}
                }}
            ]
            
            cursor = self.db.users.aggregate(pipeline)
            result = await cursor.to_list(length=1)
            
            if result:
                return {
                    "total_downloads": result[0].get("total_downloads", 0),
                    "total_uploads": result[0].get("total_uploads", 0),
                    "total_bandwidth": result[0].get("total_bandwidth", 0)
                }
            return {"total_downloads": 0, "total_uploads": 0, "total_bandwidth": 0}
            
        except Exception as e:
            logger.error(f"Error getting global stats: {e}")
            return {"total_downloads": 0, "total_uploads": 0, "total_bandwidth": 0}

    async def ban_user(self, user_id: int) -> bool:
        """Ban a user"""
        try:
            result = await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"is_banned": True, "updated_at": datetime.now(timezone.utc)}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error banning user {user_id}: {e}")
            return False

    async def unban_user(self, user_id: int) -> bool:
        """Unban a user"""
        try:
            result = await self.db.users.update_one(
                {"user_id": user_id},
                {"$set": {"is_banned": False, "updated_at": datetime.now(timezone.utc)}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error unbanning user {user_id}: {e}")
            return False

    # ============== SESSION METHODS ==============

    async def save_session(self, user_id: int, session_string: str, api_id: int,
                          api_hash: str) -> None:
        """Save user session with no expiration (permanent)."""
        try:
            now = datetime.now(timezone.utc)
            session_data = {
                "user_id": user_id,
                "session_string": session_string,
                "api_id": api_id,
                "api_hash": api_hash,
                "is_valid": True,
                "created_at": now,
                "last_used": now,
                "expires_at": None,          # 🔥 Never expires
                "updated_at": now
            }
            await self.db.sessions.update_one(
                {"user_id": user_id},
                {"$set": session_data},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error saving session for user {user_id}: {e}")
            raise

    async def get_session(self, user_id: int) -> Optional[str]:
        """Get user session string (never expires)."""
        try:
            session = await self.db.sessions.find_one({
                "user_id": user_id,
                "is_valid": True
                # No expiration check – all sessions are permanent
            })
            if session:
                # Update last used timestamp
                await self.db.sessions.update_one(
                    {"_id": session["_id"]},
                    {"$set": {"last_used": datetime.now(timezone.utc)}}
                )
                return session.get("session_string")
            return None
        except Exception as e:
            logger.error(f"Error getting session for user {user_id}: {e}")
            return None

    async def delete_session(self, user_id: int) -> bool:
        """Delete user session"""
        try:
            result = await self.db.sessions.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting session for user {user_id}: {e}")
            return False

    async def get_api_id(self, user_id: int) -> Optional[int]:
        """Get API ID for user"""
        try:
            session = await self.db.sessions.find_one(
                {"user_id": user_id},
                {"api_id": 1}
            )
            return session.get("api_id") if session else None
        except Exception as e:
            logger.error(f"Error getting API ID for user {user_id}: {e}")
            return None

    async def get_api_hash(self, user_id: int) -> Optional[str]:
        """Get API hash for user"""
        try:
            session = await self.db.sessions.find_one(
                {"user_id": user_id},
                {"api_hash": 1}
            )
            return session.get("api_hash") if session else None
        except Exception as e:
            logger.error(f"Error getting API hash for user {user_id}: {e}")
            return None

    async def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get all active sessions"""
        try:
            now = datetime.now(timezone.utc)
            cursor = self.db.sessions.find(
                {
                    "is_valid": True,
                    "$or": [
                        {"expires_at": {"$gt": now}},
                        {"expires_at": None}
                    ]
                },
                {"_id": 0, "session_string": 0}
            ).sort("last_used", DESCENDING)

            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"Error getting active sessions: {e}")
            return []

    # ============== PREFERENCES METHODS ==============

    async def save_preferences(self, user_id: int, **kwargs) -> None:
        """Save user preferences"""
        try:
            now = datetime.now(timezone.utc)

            # Handle file_filters serialization
            if "file_filters" in kwargs and isinstance(kwargs["file_filters"], dict):
                kwargs["file_filters"] = json.dumps(kwargs["file_filters"])

            preferences = {
                "user_id": user_id,
                "updated_at": now,
                **kwargs
            }

            if "created_at" not in kwargs:
                preferences["created_at"] = now

            await self.db.preferences.update_one(
                {"user_id": user_id},
                {"$set": preferences},
                upsert=True
            )

        except Exception as e:
            logger.error(f"Error saving preferences for user {user_id}: {e}")

    async def get_preferences(self, user_id: int) -> Dict[str, Any]:
        """Get user preferences"""
        try:
            prefs = await self.db.preferences.find_one(
                {"user_id": user_id},
                {"_id": 0}
            )

            if not prefs:
                return {}

            # Parse file_filters JSON
            if "file_filters" in prefs and prefs["file_filters"]:
                try:
                    prefs["file_filters"] = json.loads(prefs["file_filters"])
                except:
                    prefs["file_filters"] = {}

            return prefs

        except Exception as e:
            logger.error(f"Error getting preferences for user {user_id}: {e}")
            return {}

    async def get_caption(self, user_id: int) -> Optional[str]:
        """Get user caption"""
        prefs = await self.get_preferences(user_id)
        return prefs.get("caption")

    async def save_caption(self, user_id: int, caption: str) -> None:
        """Save user caption"""
        await self.save_preferences(user_id, caption=caption)

    async def get_thumbnail(self, user_id: int) -> Optional[str]:
        """Get user thumbnail file_id"""
        prefs = await self.get_preferences(user_id)
        return prefs.get("thumbnail_file_id")

    async def save_thumbnail(self, user_id: int, thumbnail_file_id: str) -> None:
        """Save user thumbnail"""
        await self.save_preferences(user_id, thumbnail_file_id=thumbnail_file_id)

    async def get_chat_id(self, user_id: int) -> Optional[int]:
        """Get user target chat ID"""
        prefs = await self.get_preferences(user_id)
        return prefs.get("target_chat_id")

    async def save_chat_id(self, user_id: int, chat_id: int) -> None:
        """Save user target chat ID"""
        await self.save_preferences(user_id, target_chat_id=chat_id)

    async def get_progress_style(self, user_id: int) -> str:
        """Get user progress bar style"""
        prefs = await self.get_preferences(user_id)
        return prefs.get("progress_style", "modern")

    async def save_progress_style(self, user_id: int, style: str) -> None:
        """Save user progress bar style"""
        await self.save_preferences(user_id, progress_style=style)

    async def get_file_filters(self, user_id: int) -> Dict[str, bool]:
        """Get user file filters"""
        prefs = await self.get_preferences(user_id)
        filters = prefs.get("file_filters", {})

        # Default filters (all enabled)
        default_filters = {
            "document": True,
            "video": True,
            "audio": True,
            "photo": True,
            "animation": True,
            "sticker": True,
            "voice": True,
            "zip": True
        }

        # Handle string case
        if isinstance(filters, str):
            try:
                filters = json.loads(filters)
            except:
                filters = {}

        # Merge with defaults
        return {**default_filters, **filters}

    async def save_file_filters(self, user_id: int, filters: Dict[str, bool]) -> None:
        """Save user file filters"""
        await self.save_preferences(user_id, file_filters=filters)

    async def get_file_preferences(self, user_id: int) -> Dict[str, bool]:
        """Alias for get_file_filters to match handler usage"""
        return await self.get_file_filters(user_id)

    async def save_file_preferences(self, user_id: int, filters: Dict[str, bool]) -> None:
        """Alias for save_file_filters to match handler usage"""
        await self.save_file_filters(user_id, filters)

    # ============== QUEUE STATE METHODS ==============

    async def save_queue_state(self, user_id: int, state: Dict[str, Any]) -> None:
        """Save queue state"""
        try:
            now = datetime.now(timezone.utc)

            # Serialize metadata
            if "metadata" in state and isinstance(state["metadata"], dict):
                state["metadata"] = json.dumps(state["metadata"])

            queue_data = {
                "user_id": user_id,
                "updated_at": now,
                **state
            }

            if "created_at" not in state:
                queue_data["created_at"] = now

            await self.db.queues.update_one(
                {"user_id": user_id},
                {"$set": queue_data},
                upsert=True
            )

        except Exception as e:
            logger.error(f"Error saving queue state for user {user_id}: {e}")

    async def get_queue_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get queue state"""
        try:
            queue = await self.db.queues.find_one(
                {"user_id": user_id},
                {"_id": 0}
            )

            if queue and "metadata" in queue and queue["metadata"]:
                try:
                    queue["metadata"] = json.loads(queue["metadata"])
                except:
                    queue["metadata"] = {}

            return queue

        except Exception as e:
            logger.error(f"Error getting queue state for user {user_id}: {e}")
            return None

    async def delete_queue_state(self, user_id: int) -> bool:
        """Delete queue state"""
        try:
            result = await self.db.queues.delete_one({"user_id": user_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting queue state for user {user_id}: {e}")
            return False

    async def get_all_pending_queues(self) -> List[Dict[str, Any]]:
        """Get all pending queues (incomplete)"""
        try:
            cursor = self.db.queues.find(
                {
                    "$expr": {"$lt": ["$completed_tasks", "$total_tasks"]},
                    "total_tasks": {"$gt": 0}
                },
                {"_id": 0}
            ).sort("updated_at", 1)

            queues = await cursor.to_list(length=None)

            # Parse metadata JSON
            for queue in queues:
                if "metadata" in queue and queue["metadata"]:
                    try:
                        queue["metadata"] = json.loads(queue["metadata"])
                    except:
                        queue["metadata"] = {}

            return queues

        except Exception as e:
            logger.error(f"Error getting pending queues: {e}")
            return []

    async def get_all_users_with_queues(self) -> List[Dict[str, Any]]:
        """Get all users with pending queues"""
        try:
            pending = await self.get_all_pending_queues()
            users = []
            for queue in pending:
                user_id = queue["user_id"]
                user = await self.get_user(user_id)
                if user:
                    users.append(user)
            return users
        except Exception as e:
            logger.error(f"Error getting users with queues: {e}")
            return []

    # ============== DOWNLOAD HISTORY METHODS ==============

    async def log_download(self, user_id: int, **kwargs) -> None:
        """Log download to history"""
        try:
            log_entry = {
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc),
                **kwargs
            }

            await self.db.history.insert_one(log_entry)

        except Exception as e:
            logger.error(f"Error logging download for user {user_id}: {e}")

    async def get_user_download_stats(self, user_id: int) -> Dict[str, Any]:
        """Get user download statistics"""
        try:
            pipeline = [
                {"$match": {"user_id": user_id}},
                {"$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "successful": {"$sum": {"$cond": [{"$eq": ["$success", True]}, 1, 0]}},
                    "failed": {"$sum": {"$cond": [{"$eq": ["$success", False]}, 1, 0]}},
                    "total_size": {"$sum": {"$ifNull": ["$file_size", 0]}},
                    "total_time": {"$sum": {"$ifNull": ["$download_time", 0]}}
                }}
            ]

            cursor = self.db.history.aggregate(pipeline)
            result = await cursor.to_list(length=1)

            if result:
                stats = result[0]
                stats.pop("_id", None)
                return stats
            else:
                return {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "total_size": 0,
                    "total_time": 0
                }

        except Exception as e:
            logger.error(f"Error getting user stats for {user_id}: {e}")
            return {
                "total": 0,
                "successful": 0,
                "failed": 0,
                "total_size": 0,
                "total_time": 0
            }

    # ============== STATISTICS METHODS ==============

    async def increment_stat(self, metric_name: str, value: int = 1) -> None:
        """Increment a statistic metric for today"""
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

            await self.db.statistics.update_one(
                {"metric_name": metric_name, "date": today},
                {"$inc": {"metric_value": value}, "$set": {"updated_at": datetime.now(timezone.utc)}},
                upsert=True
            )

        except Exception as e:
            logger.error(f"Error incrementing stat {metric_name}: {e}")

    async def get_statistics(self, metric_name: str, days: int = 30) -> List[Dict[str, Any]]:
        """Get statistics for a metric over last N days"""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            cursor = self.db.statistics.find(
                {
                    "metric_name": metric_name,
                    "date": {"$gte": cutoff}
                },
                {"_id": 0, "metric_name": 0}
            ).sort("date", DESCENDING)

            return await cursor.to_list(length=days)

        except Exception as e:
            logger.error(f"Error getting statistics for {metric_name}: {e}")
            return []

    # ============== BACKUP METHODS ==============

    async def log_backup(self, backup_file: str, size: int, status: str = "success",
                        error: str = None) -> None:
        """Log backup operation"""
        try:
            backup_log = {
                "backup_file": backup_file,
                "size": size,
                "status": status,
                "error": error,
                "created_at": datetime.now(timezone.utc)
            }

            await self.db.backups.insert_one(backup_log)

        except Exception as e:
            logger.error(f"Error logging backup: {e}")

    async def get_backup_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent backup history"""
        try:
            cursor = self.db.backups.find(
                {},
                {"_id": 0}
            ).sort("created_at", DESCENDING).limit(limit)

            return await cursor.to_list(length=limit)

        except Exception as e:
            logger.error(f"Error getting backup history: {e}")
            return []

    async def cleanup_old_backup_logs(self, days: int = 30) -> int:
        """Clean up backup logs older than N days"""
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            result = await self.db.backups.delete_many({"created_at": {"$lt": cutoff}})
            return result.deleted_count
        except Exception as e:
            logger.error(f"Error cleaning old backup logs: {e}")
            return 0

    # ============== HEALTH CHECK ==============

    async def ping(self) -> bool:
        """Check database connection"""
        try:
            await self.client.admin.command('ping')
            return True
        except Exception:
            return False

    async def get_db_stats(self) -> Dict[str, Any]:
        """Get database statistics"""
        try:
            db_stats = await self.db.command("dbStats")
            collection_stats = {}

            collections = await self.db.list_collection_names()
            for collection in collections:
                count = await self.db[collection].count_documents({})
                collection_stats[collection] = count

            return {
                "database": MONGODB_DB_NAME,
                "collections": collection_stats,
                "data_size": db_stats.get("dataSize", 0),
                "storage_size": db_stats.get("storageSize", 0),
                "indexes": db_stats.get("indexes", 0),
                "index_size": db_stats.get("indexSize", 0)
            }

        except Exception as e:
            logger.error(f"Error getting DB stats: {e}")
            return {}


# Global MongoDB instance
db = MongoDB()


# ============== INITIALIZATION ==============

async def init_db():
    """Initialize database connection"""
    await db.connect()


async def close_db():
    """Close database connection"""
    await db.disconnect()