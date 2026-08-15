"""
User Session Management Service
"""

import asyncio
from typing import Dict, Optional, List
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.errors import (
    AuthKeyDuplicated, SessionExpired, SessionRevoked,
    AuthKeyInvalid, AuthKeyUnregistered
)

from config import (
    API_ID, API_HASH, LOGIN_SYSTEM, STRING_SESSION,
    WORKERS, SLEEP_THRESHOLD, MAX_CONCURRENT_TRANSMISSIONS
)
from database.mongodb import db
from plugins.security.encryption import decrypt_data
from plugins.core.utils import get_logger, session_cache, TTLCache
from plugins.monitoring.metrics import usage_stats

logger = get_logger(__name__)


class UserSessionManager:
    """Manage multiple user sessions efficiently"""
    
    def __init__(self):
        self.sessions: Dict[int, Client] = {}
        self.session_locks: Dict[int, asyncio.Lock] = {}
        self.global_user_client: Optional[Client] = None
        
        # Initialize global user client if using string session
        if STRING_SESSION and not LOGIN_SYSTEM:
            self._init_global_client()
    
    def _init_global_client(self) -> None:
        """Initialize global user client"""
        try:
            self.global_user_client = Client(
                "user_client",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=STRING_SESSION,
                in_memory=True,
                no_updates=True,
                workers=WORKERS,
                sleep_threshold=SLEEP_THRESHOLD,
                max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
            )

            logger.info("Global user client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize global user client: {e}")
            self.global_user_client = None
    
    async def get_session(self, user_id: int) -> Optional[Client]:
        """Get or create a user session"""
        # Check cache first
        cached_session = session_cache.get(f"session_{user_id}")
        if cached_session:
            return cached_session
        
        # Check if user is banned
        user = await db.get_user(user_id)
        if user and user.get("is_banned"):
            logger.warning(f"Banned user {user_id} attempted to get session")
            return None
        
        # Get or create lock for this user
        if user_id not in self.session_locks:
            self.session_locks[user_id] = asyncio.Lock()
        
        async with self.session_locks[user_id]:
            # Check again after acquiring lock
            if user_id in self.sessions:
                session = self.sessions[user_id]
                # Verify session is still valid
                if await self._verify_session(session):
                    session_cache.set(f"session_{user_id}", session)
                    return session
                else:
                    await self.remove_session(user_id)
            
            # Create new session
            session = await self._create_session(user_id)
            if session:
                self.sessions[user_id] = session
                session_cache.set(f"session_{user_id}", session)
                logger.debug(f"Created new session for user {user_id}")
                
                # Update metrics
                usage_stats.increment("active_sessions")
                
                return session
            
            return None
    
    async def _create_session(self, user_id: int) -> Optional[Client]:
        """Create a new user session"""
        try:
            if LOGIN_SYSTEM:
                # Create session from database
                user_data = await db.get_session(user_id)
                if not user_data:
                    logger.warning(f"No session data found for user {user_id}")
                    return None
                
                # Get API credentials
                db_api_id = await db.get_api_id(user_id)
                api_id = int(db_api_id) if db_api_id is not None else API_ID
                api_hash = await db.get_api_hash(user_id) or API_HASH
                
                # Decrypt session string
                try:
                    decrypted_session = decrypt_data(user_data)
                except Exception as e:
                    logger.error(f"Failed to decrypt session for user {user_id}: {e}")
                    await db.delete_session(user_id)
                    return None
                
                # Create client
                session = Client(
                    f"user_{user_id}",
                    session_string=decrypted_session,
                    api_id=api_id,
                    api_hash=api_hash,
                    in_memory=True,
                    no_updates=True,
                    workers=WORKERS,
                    sleep_threshold=SLEEP_THRESHOLD,
                    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
                )

                
                # Test connection
                try:
                    await session.connect()
                    # Verify session works by getting some basic info
                    me = await session.get_me()
                    if not me:
                        raise Exception("Failed to get user info")
                    
                    logger.debug(f"Session verified for user {user_id}: @{me.username}")
                    return session
                except (SessionExpired, SessionRevoked, AuthKeyInvalid, AuthKeyUnregistered) as e:
                    logger.warning(f"Session invalidated by Telegram for user {user_id}. Reason: {type(e).__name__} - {e}")
                    await db.delete_session(user_id)
                    return None
                except Exception as e:
                    logger.error(f"Failed to connect session for user {user_id}: {e}")
                    return None
            else:
                # Return global user client
                if self.global_user_client:
                    # Ensure client is started
                    if not self.global_user_client.is_connected:
                        try:
                            await self.global_user_client.start()
                        except Exception as e:
                            logger.error(f"Failed to start global user client: {e}")
                            return None
                    return self.global_user_client
                return None
                
        except Exception as e:
            logger.error(f"Error creating session for user {user_id}: {e}", exc_info=True)
            return None
    
    async def _verify_session(self, session: Client) -> bool:
        """Verify if session is still valid"""
        if not session:
            return False
        
        try:
            if not session.is_connected:
                await session.connect()
            
            # Try to get basic info
            me = await session.get_me()
            return me is not None
        except (SessionExpired, SessionRevoked, AuthKeyInvalid, AuthKeyUnregistered):
            return False
        except Exception:
            return False
    
    async def remove_session(self, user_id: int) -> None:
        """Remove user session"""
        if user_id in self.sessions:
            try:
                session = self.sessions[user_id]
                if session.is_connected:
                    await session.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting session for user {user_id}: {e}")
            
            del self.sessions[user_id]
            
            session_cache.delete(f"session_{user_id}")
            
            # Update metrics
            usage_stats.decrement("active_sessions")
            
            logger.info(f"Removed session for user {user_id}")
    
    async def close_all_sessions(self) -> None:
        """Close all user sessions"""
        logger.info(f"Closing {len(self.sessions)} active sessions...")
        
        for user_id in list(self.sessions.keys()):
            await self.remove_session(user_id)
        
        # Close global client
        if self.global_user_client and self.global_user_client.is_connected:
            try:
                await self.global_user_client.stop()
                logger.info("Global user client stopped")
            except Exception as e:
                logger.error(f"Error stopping global user client: {e}")
    
    async def get_active_session_count(self) -> int:
        """Get number of active sessions"""
        return len(self.sessions)
    
    async def get_active_users(self) -> List[int]:
        """Get list of active user IDs"""
        return list(self.sessions.keys())
    
    async def is_session_active(self, user_id: int) -> bool:
        """Check if user has active session"""
        if user_id in self.sessions:
            session = self.sessions[user_id]
            return await self._verify_session(session)
        return False


# Global session manager instance
session_manager = UserSessionManager()