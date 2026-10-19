"""
Authentication and Authorization
"""

from typing import Optional, List, Union
from datetime import datetime, timedelta

from pyrogram.types import Message, CallbackQuery

from config import ADMINS
from database.mongodb import db
from plugins.core.utils import get_logger, user_cache

logger = get_logger(__name__)


class AuthorizationManager:
    """Manage user authorization and permissions"""
    
    def __init__(self):
        self.admin_ids = set(ADMINS) if ADMINS else set()
        self.banned_cache = {}
        self.cache_ttl = 300  # 5 minutes
    
    async def is_authorized(self, user_id: int) -> bool:
        """Check if user is authorized to use the bot"""
        # Admins are always authorized
        if self.is_admin(user_id):
            return True
        
        # Check cache
        cache_key = f"auth_{user_id}"
        cached = user_cache.get(cache_key)
        if cached is not None:
            return cached
        
        try:
            # Check if user is banned
            if await self.is_banned(user_id):
                user_cache.set(cache_key, False)
                return False
            
            # All non-banned users are authorized by default
            user_cache.set(cache_key, True)
            return True
            
        except Exception as e:
            logger.error(f"Error checking authorization for user {user_id}: {e}")
            return False  # Fail secure
    
    async def is_banned(self, user_id: int) -> bool:
        """Check if user is banned"""
        # Check cache
        if user_id in self.banned_cache:
            timestamp, is_banned = self.banned_cache[user_id]
            if datetime.now().timestamp() - timestamp < self.cache_ttl:
                return is_banned
        
        try:
            user = await db.get_user(user_id)
            is_banned = user.get("is_banned", False) if user else False
            
            # Update cache
            self.banned_cache[user_id] = (datetime.now().timestamp(), is_banned)
            
            return is_banned
            
        except Exception as e:
            logger.error(f"Error checking ban status for user {user_id}: {e}")
            return False
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return user_id in self.admin_ids
    
    async def ban_user(self, user_id: int, admin_id: int) -> bool:
        """Ban a user"""
        try:
            # Don't allow banning admins
            if self.is_admin(user_id):
                logger.warning(f"Admin {admin_id} attempted to ban another admin {user_id}")
                return False
            
            await db.ban_user(user_id)
            
            # Update cache
            self.banned_cache[user_id] = (datetime.now().timestamp(), True)
            user_cache.delete(f"auth_{user_id}")
            
            logger.info(f"User {user_id} banned by admin {admin_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error banning user {user_id}: {e}")
            return False
    
    async def unban_user(self, user_id: int, admin_id: int) -> bool:
        """Unban a user"""
        try:
            await db.unban_user(user_id)
            
            # Update cache
            self.banned_cache[user_id] = (datetime.now().timestamp(), False)
            user_cache.delete(f"auth_{user_id}")
            
            logger.info(f"User {user_id} unbanned by admin {admin_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error unbanning user {user_id}: {e}")
            return False
    
    async def add_admin(self, user_id: int, added_by: int) -> bool:
        """Add a new admin"""
        try:
            self.admin_ids.add(user_id)
            logger.info(f"Admin {user_id} added by {added_by}")
            return True
        except Exception as e:
            logger.error(f"Error adding admin {user_id}: {e}")
            return False
    
    async def remove_admin(self, user_id: int, removed_by: int) -> bool:
        """Remove an admin"""
        try:
            self.admin_ids.discard(user_id)
            logger.info(f"Admin {user_id} removed by {removed_by}")
            return True
        except Exception as e:
            logger.error(f"Error removing admin {user_id}: {e}")
            return False
    
    async def require_auth(self, user_id: int, message: Union[Message, CallbackQuery]) -> bool:
        """Decorator-like function to require authentication"""
        if not await self.is_authorized(user_id):
            from plugins.core.constants import ERROR_MESSAGES
            error_text = ERROR_MESSAGES.get("access_denied", "⛔ Access Denied")
            
            if isinstance(message, Message):
                await message.reply(error_text)
            elif isinstance(message, CallbackQuery):
                await message.answer("⛔ Access Denied", show_alert=True)
            
            return False
        return True


# Global authorization manager
auth_manager = AuthorizationManager()


def is_authorized(user_id: int) -> bool:
    """Legacy function for backward compatibility"""
    return asyncio.run(auth_manager.is_authorized(user_id))