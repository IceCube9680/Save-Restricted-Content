"""
Message Handlers for the Bot
Handles text messages, media, and download links
"""

import os
import time
import asyncio
import random
from typing import Optional, Dict, Any, Tuple, Union, List
from datetime import datetime
import pyrogram
from plugins.handlers.commands import user_sessions, handle_login_steps
from pyrogram import Client, filters, enums, utils
from pyrogram.errors import (
    FloodWait, UserIsBlocked, InputUserDeactivated, 
    UserAlreadyParticipant, InviteHashExpired, UsernameNotOccupied,
    ChatAdminRequired, ChatWriteForbidden, PhoneNumberInvalid,
    PhoneCodeInvalid, PhoneCodeExpired, SessionPasswordNeeded,
    PasswordHashInvalid, AuthKeyDuplicated, FileReferenceExpired
)
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType

from config import (
    LOGIN_SYSTEM, STRING_SESSION, ERROR_MESSAGE, WAITING_TIME,
    CHANNEL_ID, ENABLE_GLOBAL_CHANNEL, GLOBAL_CHANNEL_ID,
    MAX_BATCH_SIZE, DOWNLOAD_TIMEOUT, UPLOAD_TIMEOUT, ADMINS,
    LOG_CHANNEL, UPDATE_INTERVAL
)

from database.mongodb import db
from plugins.security.auth import auth_manager, is_authorized
from plugins.security.encryption import encrypt_data
from plugins.core.utils import (
    get_logger, humanbytes, time_formatter, get_ist_time,
    truncate_text, rate_limiter, get_downloads_dir,
    safe_delete_file, validate_telegram_link, ensure_directory,
    sanitize_filename
)
from plugins.core.models import FileTask, TaskStatus, BatchCancel, LinkInfo, DownloadResult
from plugins.core.animations import ProgressAnimations
from plugins.services.queue_manager import queue_manager
from plugins.services.session_manager import session_manager
from plugins.services.downloader import download_service
from plugins.services.uploader import upload_service
from plugins.progress_display import progress_callback
from plugins.monitoring.metrics import usage_stats
from plugins.handlers.callbacks import handle_progress_controls

logger = get_logger(__name__)

# Patch Pyrogram to support newer channel IDs
utils.MIN_CHANNEL_ID = -100999999999999


# Global variables for batch processing
batch_temp = type('BatchTemp', (), {'IS_BATCH': {}})()
user_states: Dict[int, Dict[str, Any]] = {}


# ============== MESSAGE FILTERS ==============

@Client.on_message(filters.command("logs") & filters.user(ADMINS))
async def send_logs(client: Client, message: Message):
    """Send bot log file"""
    log_file = "bot.log"
    if not os.path.exists(log_file) and os.path.exists("logs"):
        files = [os.path.join("logs", f) for f in os.listdir("logs") if f.endswith(".log")]
        if files:
            log_file = max(files, key=os.path.getmtime)
    
    if os.path.exists(log_file):
        await message.reply_document(
            document=log_file,
            caption=f"📜 **System Logs**\n\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            file_name="logs.txt"
        )
    else:
        await message.reply_text("❌ **Log file not found**")

@Client.on_message(filters.private)
async def handle_private_messages(client: Client, message: Message):
    """Handle all private text messages"""
    user_id = message.from_user.id
    
    # Check authorization
    if not await auth_manager.require_auth(user_id, message):
        return
    
    # Rate limiting
    if not rate_limiter.is_allowed(user_id):
        wait_time = rate_limiter.get_wait_time(user_id)
        await message.reply_text(
            f"⏳ **Rate Limit Exceeded**\n\nPlease wait {wait_time:.0f} seconds before trying again."
        )
        return
    
    # Add user to database if not exists
    if not await db.is_user_exist(user_id):
        await db.add_user(
            user_id,
            message.from_user.first_name,
            message.from_user.username,
            message.from_user.last_name
        )
        
        # Notify about new user
        if LOG_CHANNEL:
            try:
                mention = message.from_user.mention
                username = f"@{message.from_user.username}" if message.from_user.username else "None"
                await client.send_message(
                    int(LOG_CHANNEL),
                    f"#NEW_USER\n\n"
                    f"**New User Started The Bot** 🚀\n\n"
                    f"**User:** {mention}\n"
                    f"**ID:** `{user_id}`\n"
                    f"**Username:** {username}"
                )
            except Exception as e:
                logger.error(f"Failed to send new user notification: {e}")
    
    # Update user activity
    await db.update_user_activity(user_id)
    
    # Check if user is in a state (login, settings, etc.)
    # First, check if user is in login process
    if user_id in user_sessions:
        await handle_login_steps(client, message)
        return

    # Then check other states (settings, etc.)
    if user_id in user_states:
        await handle_user_state(client, message, user_states[user_id])
        return
    
    if not message.text:
        return
    
    # Handle join chat links
    if "https://t.me/+" in message.text or "https://t.me/joinchat/" in message.text:
        await handle_join_chat(client, message)
        return
    
    # Handle download links
    if "https://t.me/" in message.text:
        await handle_download_link(client, message)
        return
    
    # Handle normal text messages
    await message.reply_text(
        "❓ **Unknown command**\n\n"
        "Send a Telegram link to download content.\n"
        "Use /help for more information."
    )


# ============== USER STATE HANDLING ==============

async def handle_user_state(client: Client, message: Message, state: Dict[str, Any]):
    """Handle user in a specific state (login, settings, etc.)"""
    user_id = message.from_user.id
    
    # Check if message is a command to break out of state
    if message.text and message.text.startswith("/") and message.text.lower() != "/cancel":
        del user_states[user_id]
        await message.reply_text("❌ Action cancelled.")
        return

    action = state.get("action")
    
    if action == "login_api_id":
        await handle_login_api_id(client, message, state)
    elif action == "login_api_hash":
        await handle_login_api_hash(client, message, state)
    elif action == "login_phone":
        await handle_login_phone(client, message, state)
    elif action == "login_code":
        await handle_login_code(client, message, state)
    elif action == "login_password":
        await handle_login_password(client, message, state)
    elif action == "set_caption":
        await handle_set_caption(client, message)
    elif action == "set_thumbnail":
        await handle_set_thumbnail(client, message)
    elif action == "set_chat":
        await handle_set_chat(client, message)
    else:
        # Unknown state, clear it
        del user_states[user_id]
        await message.reply_text("❌ Login session expired or invalid. Please try /login again.")


# ============== LOGIN HANDLERS ==============

async def handle_login_api_id(client: Client, message: Message, state: Dict[str, Any]):
    """Handle API ID submission"""
    user_id = message.from_user.id
    
    if not message.text:
        await message.reply_text("❌ Please send a valid API ID.")
        return
    
    text = message.text.strip()
    
    if text.lower() == "/cancel":
        del user_states[user_id]
        await message.reply_text("❌ Login cancelled.")
        return
        
    if not text.isdigit():
        await message.reply_text("❌ API ID must be a number.")
        return
        
    user_states[user_id].update({
        "action": "login_api_hash",
        "api_id": int(text)
    })
    await message.reply_text("2. Send me your API HASH (from my.telegram.org)")


async def handle_login_api_hash(client: Client, message: Message, state: Dict[str, Any]):
    """Handle API Hash submission"""
    user_id = message.from_user.id
    
    if not message.text:
        await message.reply_text("❌ Please send a valid API Hash.")
        return
        
    text = message.text.strip()
    
    if text.lower() == "/cancel":
        del user_states[user_id]
        await message.reply_text("❌ Login cancelled.")
        return
        
    user_states[user_id].update({
        "action": "login_phone",
        "api_hash": text
    })
    await message.reply_text("📱 Please send your phone number with country code (e.g., +1234567890).")


async def handle_login_phone(client: Client, message: Message, state: Dict[str, Any]):
    """Handle phone number submission for login"""
    user_id = message.from_user.id
    
    if not message.text:
        await message.reply_text("❌ Please send a valid phone number.")
        return
    phone = message.text.strip()
    
    if phone.lower() == "/cancel":
        del user_states[user_id]
        await message.reply_text("❌ Login cancelled.")
        return
    
    try:
        # Create temporary client for login
        temp_client = Client(
            f"temp_{user_id}",
            api_id=state.get("api_id"),
            api_hash=state.get("api_hash"),
            in_memory=True
        )
        
        await temp_client.connect()
        
        # Send code
        sent_code = await temp_client.send_code(phone)
        
        # Update state
        user_states[user_id] = {
            "action": "login_code",
            "client": temp_client,
            "phone": phone,
            "phone_code_hash": sent_code.phone_code_hash,
            "api_id": state.get("api_id"),
            "api_hash": state.get("api_hash")
        }
        
        await message.reply_text(
            "📱 **Verification Code Sent**\n\n"
            "Please check your Telegram app and send me the 5-digit code you received.\n\n"
            "Type /cancel to abort."
        )
        
    except PhoneNumberInvalid:
        await message.reply_text("❌ Invalid phone number. Please check and try again.")
        del user_states[user_id]
    except FloodWait as e:
        await message.reply_text(f"⏳ Too many attempts. Please wait {e.value} seconds.")
        del user_states[user_id]
    except Exception as e:
        logger.error(f"Login phone error: {e}")
        await message.reply_text(f"❌ Error: {str(e)[:200]}")
        del user_states[user_id]


async def handle_login_code(client: Client, message: Message, state: Dict[str, Any]):
    """Handle verification code submission"""
    user_id = message.from_user.id
    
    if not message.text:
        await message.reply_text("❌ Please send the code.")
        return
    code = message.text.strip().replace(" ", "")
    
    if code.lower() == "/cancel":
        temp_client = state.get("client")
        if temp_client:
            try:
                await temp_client.disconnect()
            except Exception:
                pass
        del user_states[user_id]
        await message.reply_text("❌ Login cancelled.")
        return
    
    try:
        temp_client = state["client"]
        
        # Try to sign in
        signed_in = await temp_client.sign_in(
            phone_number=state["phone"],
            phone_code_hash=state["phone_code_hash"],
            phone_code=code
        )
        
        # Check if 2FA is enabled
        if isinstance(signed_in, bool) and not signed_in:
            # 2FA required
            user_states[user_id] = {
                "action": "login_password",
                "client": temp_client,
                "phone": state["phone"],
                "phone_code_hash": state["phone_code_hash"],
                "api_id": state.get("api_id"),
                "api_hash": state.get("api_hash")
            }
            
            await message.reply_text(
                "🔐 **Two-Step Verification Enabled**\n\n"
                "Please enter your password."
            )
            return
        
        # Login successful
        session_string = await temp_client.export_session_string()
        
        # Encrypt and save session
        encrypted_session = encrypt_data(session_string)
        await db.save_session(
            user_id,
            encrypted_session,
            state["api_id"],
            state["api_hash"]
        )
        
        # Cleanup
        await temp_client.disconnect()
        del user_states[user_id]
        
        await message.reply_text(
            "✅ **Login Successful!**\n\n"
            "You can now download restricted content.\n"
            "Use /logout to logout."
        )
        
        # Update metrics
        usage_stats.increment("active_sessions")
        
    except PhoneCodeInvalid:
        await message.reply_text("❌ Invalid code. Please try again.")
    except PhoneCodeExpired:
        await message.reply_text("❌ Code expired. Please start over with /login.")
        del user_states[user_id]
    except FloodWait as e:
        await message.reply_text(f"⏳ Too many attempts. Please wait {e.value} seconds.")
    except Exception as e:
        logger.error(f"Login code error: {e}")
        await message.reply_text(f"❌ Error: {str(e)[:200]}")


async def handle_login_password(client: Client, message: Message, state: Dict[str, Any]):
    """Handle 2FA password submission"""
    user_id = message.from_user.id
    
    if not message.text:
        await message.reply_text("❌ Please send the password.")
        return
    password = message.text
    
    if password.strip().lower() == "/cancel":
        temp_client = state.get("client")
        if temp_client:
            try:
                await temp_client.disconnect()
            except Exception:
                pass
        del user_states[user_id]
        await message.reply_text("❌ Login cancelled.")
        return
    
    try:
        temp_client = state["client"]
        
        # Check password
        await temp_client.check_password(password)
        
        # Login successful
        session_string = await temp_client.export_session_string()
        
        # Encrypt and save session
        encrypted_session = encrypt_data(session_string)
        await db.save_session(
            user_id,
            encrypted_session,
            state["api_id"],
            state["api_hash"]
        )
        
        # Cleanup
        await temp_client.disconnect()
        del user_states[user_id]
        
        await message.reply_text(
            "✅ **Login Successful!**\n\n"
            "You can now download restricted content.\n"
            "Use /logout to logout."
        )
        
        # Update metrics
        usage_stats.increment("active_sessions")
        
    except PasswordHashInvalid:
        await message.reply_text("❌ Invalid password. Please try again.")
    except FloodWait as e:
        await message.reply_text(f"⏳ Too many attempts. Please wait {e.value} seconds.")
    except Exception as e:
        logger.error(f"Login password error: {e}")
        await message.reply_text(f"❌ Error: {str(e)[:200]}")


# ============== SETTINGS HANDLERS ==============

async def handle_set_caption(client: Client, message: Message):
    """Handle caption setting"""
    user_id = message.from_user.id
    
    if not message.text:
        await message.reply_text("❌ Please send text for caption.")
        return
    caption = message.text
    
    if caption == "/cancel":
        del user_states[user_id]
        await message.reply_text("❌ Caption setting cancelled.")
        return
    
    # Save caption
    await db.save_preferences(user_id, caption=caption)
    
    del user_states[user_id]
    
    await message.reply_text(
        "✅ **Caption saved successfully!**\n\n"
        f"Your caption:\n`{caption[:100]}{'...' if len(caption) > 100 else ''}`"
    )


async def handle_set_thumbnail(client: Client, message: Message):
    """Handle thumbnail setting"""
    user_id = message.from_user.id

    if message.text:
        if message.text == "/cancel":
            del user_states[user_id]
            await message.reply_text("❌ Thumbnail setting cancelled.")
            return
        
        if message.text.lower() in ["no", "none", "remove", "delete"]:
            await db.save_preferences(user_id, thumbnail_file_id=None)
            del user_states[user_id]
            await message.reply_text("✅ Thumbnail removed!")
            return
            
        if "https://t.me/" in message.text:
            del user_states[user_id]
            await message.reply_text("❌ Thumbnail setting cancelled. Please resend your link to download.")
            return
    
    if not message.photo and not message.document:
        await message.reply_text(
            "❌ **Please send an image file**\n\n"
            "Send a photo or document (JPG, PNG, WEBP) to use as thumbnail."
        )
        return
    
    # Get file ID
    if message.photo:
        file_id = message.photo.file_id
    elif message.document:
        # Check if it's an image
        mime = message.document.mime_type or ""
        if not mime.startswith("image/"):
            await message.reply_text("❌ Please send an image file.")
            return
        file_id = message.document.file_id
    else:
        await message.reply_text("❌ Please send an image file.")
        return
    
    # Save thumbnail
    await db.save_preferences(user_id, thumbnail_file_id=file_id)
    
    del user_states[user_id]
    
    await message.reply_text(
        "✅ **Thumbnail saved successfully!**\n\n"
        "This thumbnail will be used for all video/document uploads."
    )


async def handle_set_chat(client: Client, message: Message):
    """Handle target chat setting"""
    user_id = message.from_user.id
    
    if message.text == "/cancel":
        del user_states[user_id]
        await message.reply_text("❌ Target chat setting cancelled.")
        return
    
    # Check if it's a forwarded message
    if not message.forward_from_chat:
        await message.reply_text(
            "❌ **Please forward a message from the target chat**\n\n"
            "1. Go to the channel/group where you want files uploaded\n"
            "2. Forward any message from that chat to me\n"
            "3. I'll save that chat as your default upload destination"
        )
        return
    
    chat = message.forward_from_chat
    chat_id = chat.id
    
    # Save chat ID
    await db.save_preferences(user_id, target_chat_id=chat_id)
    
    del user_states[user_id]
    
    chat_name = chat.title or f"Chat {chat_id}"
    
    await message.reply_text(
        f"✅ **Target Chat Saved!**\n\n"
        f"All files will be uploaded to:\n"
        f"📢 **{chat_name}**\n"
        f"🆔 `{chat_id}`"
    )


# ============== JOIN CHAT HANDLER ==============

async def handle_join_chat(client: Client, message: Message):
    """Handle join chat links"""
    user_id = message.from_user.id

    # Check if user client is available
    acc = await get_user_client(user_id)
    if not acc:
        if LOGIN_SYSTEM:
            await message.reply_text(
                "🔐 **Login Required**\n\n"
                "You need to login first to join private chats.\n"
                "Use /login to get started."
            )
        else:
            await message.reply_text(
                "❌ **String Session Not Configured**\n\n"
                "The bot owner hasn't set up a string session."
            )
        return

    try:
        await acc.join_chat(message.text)
        await message.reply_text("✅ Successfully joined the chat!")
        
    except UserAlreadyParticipant:
        await message.reply_text("✅ You are already a member of this chat.")
        
    except InviteHashExpired:
        await message.reply_text("❌ This invite link has expired.")
        
    except UsernameNotOccupied:
        await message.reply_text("❌ Invalid chat username or link.")
        
    except FloodWait as e:
        await message.reply_text(f"⏳ Too many attempts. Please wait {e.value} seconds.")
        
    except Exception as e:
        logger.error(f"Join chat error: {e}")
        await message.reply_text(f"❌ Failed to join: {str(e)[:200]}")


# ============== DOWNLOAD LINK HANDLER ==============

async def handle_download_link(client: Client, message: Message):
    """Handle download links for content"""
    user_id = message.from_user.id
    
    # Validate link
    if not validate_telegram_link(message.text):
        await message.reply_text(
            "❌ **Invalid Telegram Link**\n\n"
            "Please provide a valid Telegram message link.\n"
            "Example: `https://t.me/channel/123`"
        )
        return
    
    # Check if user already has active batch
    if batch_temp.IS_BATCH.get(user_id) is False:
        await message.reply_text(
            "⏳ **One task is already processing.**\n\n"
            "Please wait for it to complete or use /cancel to stop it."
        )
        return
    
    # Show starting animation
    starting_msg = await message.reply_text(
        "🚀 **Starting download...**\n\n"
        "🔍 Analyzing link..."
    )
    
    batch_temp.IS_BATCH.setdefault(user_id, True)
    
    try:
        # Parse link
        link_info = parse_telegram_link(message.text)
        if not link_info:
            await starting_msg.delete()
            await message.reply_text("❌ Failed to parse link.")
            return
        
        # Check login requirement for private content
        if link_info.type == "private" and not await check_user_session(user_id):
            await starting_msg.delete()
            await message.reply_text(
                "🔐 **Login Required**\n\n"
                "This link is from a private chat.\n"
                "Please use /login first to access restricted content."
            )
            return
        
        await starting_msg.delete()
        batch_temp.IS_BATCH[user_id] = False
        
        # Create tasks
        tasks = []
        for msgid in range(link_info.from_id, link_info.to_id + 1):
            task = FileTask(
                message_id=message.id,
                chat_id=message.chat.id,
                msgid=msgid,
                from_user_id=user_id
            )
            tasks.append(task)
        
        # Add batch to queue
        queue = await queue_manager.add_batch(
            user_id,
            tasks,
            message.chat.id,
            metadata={
                "type": link_info.type, 
                "source_id": link_info.source_id,
                "from_id": link_info.from_id,
                "to_id": link_info.to_id,
                "chat_id": message.chat.id,
                "link": message.text[:100]
            }
        )

        # Send initial progress message
        is_batch = link_info.is_batch
        title = "INITIALIZING BATCH DOWNLOAD" if is_batch else "INITIALIZING DOWNLOAD"
        
        progress_msg = await client.send_message(
            message.chat.id,
            f"""
🎬 **{title}** 🎬

━━━━━━━━━━━━━━━━━━━━
🔍 Analyzing: `{truncate_text(message.text, 50)}`
📁 Files: {link_info.batch_size}
⏳ Preparing download queue...
━━━━━━━━━━━━━━━━━━━━
🔄 Setting up connections...
⚙️ Optimizing performance...
━━━━━━━━━━━━━━━━━━━━
🚀 **Starting in 3... 2... 1...**
""",
            reply_to_message_id=message.id
         )
        queue.progress_message_id = progress_msg.id
        
        # Brief animation before starting
        await asyncio.sleep(2)
        
        # Start batch processing
        asyncio.create_task(process_batch(client, user_id))
        
    except Exception as e:
        logger.error(f"Download link error: {e}")
        try:
            await starting_msg.delete()
        except Exception:
            pass
        try:
            await message.reply_text(f"❌ Error: {str(e)[:200]}")
        except Exception:
            pass
        batch_temp.IS_BATCH[user_id] = True


def parse_telegram_link(link: str) -> Optional[LinkInfo]:
    """Parse Telegram link and extract information"""
    info = LinkInfo()
    
    # Remove query parameters
    link = link.split("?")[0].strip()
    
    # Split by /
    parts = [p for p in link.split("/") if p]
    
    try:
        if "t.me/c/" in link:
            # Private channel: e.g. https://t.me/c/1234567890/100 or https://t.me/c/1234567890/55/100
            info.type = "private"
            c_index = parts.index("c") if "c" in parts else -1
            if c_index != -1 and len(parts) > c_index + 1:
                info.source_id = f"-100{parts[c_index + 1]}"
                
                # Check for topic/thread ID (e.g., /c/channel_id/topic_id/msg_id)
                if len(parts) >= c_index + 4 and parts[c_index + 2].isdigit():
                    range_part = parts[c_index + 3]
                elif len(parts) >= c_index + 3:
                    range_part = parts[c_index + 2]
                else:
                    range_part = "0"
                    
                range_parts = range_part.split("-")
                info.from_id = int(range_parts[0])
                info.to_id = int(range_parts[1]) if len(range_parts) > 1 else info.from_id
                
        elif "t.me/b/" in link:
            # Bot message
            info.type = "bot"
            b_index = parts.index("b") if "b" in parts else -1
            if b_index != -1 and len(parts) > b_index + 1:
                info.bot_username = parts[b_index + 1]
                info.source_id = info.bot_username
                range_part = parts[b_index + 2] if len(parts) > b_index + 2 else "0"
                range_parts = range_part.split("-")
                info.from_id = int(range_parts[0])
                info.to_id = int(range_parts[1]) if len(range_parts) > 1 else info.from_id
                
        elif "t.me/+" in link or "t.me/joinchat/" in link:
            # Join chat link
            info.type = "join_chat"
            info.invite_hash = parts[-1]
            
        else:
            # Public channel/group: e.g. https://t.me/channel/100 or https://t.me/channel/55/100
            info.type = "public"
            domain_index = -1
            for idx, p in enumerate(parts):
                if "t.me" in p or "telegram.me" in p:
                    domain_index = idx
                    break
            
            if domain_index != -1 and len(parts) > domain_index + 1:
                info.source_id = parts[domain_index + 1]
                
                if len(parts) >= domain_index + 4 and parts[domain_index + 2].isdigit():
                    range_part = parts[domain_index + 3]
                elif len(parts) >= domain_index + 3:
                    range_part = parts[domain_index + 2]
                else:
                    range_part = "0"
                    
                range_parts = range_part.split("-")
                info.from_id = int(range_parts[0])
                info.to_id = int(range_parts[1]) if len(range_parts) > 1 else info.from_id
                
        return info

        
    except Exception as e:
        logger.error(f"Link parsing error: {e}")
        return None


async def check_user_session(user_id: int) -> bool:
    """Check if user has valid session for private content"""
    if LOGIN_SYSTEM:
        session = await db.get_session(user_id)
        return session is not None
    else:
        return STRING_SESSION is not None


# ============== BATCH PROCESSING ==============

async def process_batch(client: Client, user_id: int):
    """Process batch of download tasks"""
    queue = queue_manager.get_queue(user_id)
    
    if not queue.metadata:
        logger.warning(f"No metadata for user {user_id}")
        return
    
    source_type = queue.metadata.get("type")
    source_id = queue.metadata.get("source_id")

    # Get user client
    acc = await get_user_client(user_id)
    if not acc and source_type != "public":
        await client.send_message(
            queue.chat_id,
            "❌ **Connection Error**\n\n"
            "Unable to establish connection.\n"
            "Please check your login session with /login"
        )
        return
    
    if acc:
        try:
            if not acc.is_connected:
                await acc.start()
        except Exception as e:
            logger.error(f"Failed to start user client: {e}")
            if source_type != "public":
                await client.send_message(
                    queue.chat_id,
                    f"❌ **Connection Error**\n\nFailed to start client: {e}"
                )
                batch_temp.IS_BATCH[user_id] = True
                await cleanup_user_client(acc)
                return

    try:
        saved = 0
        errors = 0
        
        while queue.queue or queue.current_task:
            # Check if paused
            if queue.is_paused:
                await asyncio.sleep(2)
                continue
            
            # Start next task
            task = await queue_manager.start_next_task(user_id)
            if not task:
                break
            
            success = False
            try:
                if source_type == "public":
                    # Public content - forward/copy or download/upload to target chat
                    success = await handle_public_content(client, acc, source_id, task, queue.chat_id)
                else:
                    # Private content - need user client
                    mock_msg = create_mock_message(queue.chat_id, user_id)
                    success = await handle_private(
                        client, acc, mock_msg,
                        int(source_id) if source_type == "private" else source_id,
                        task.msgid, task
                    )
                    
            except BatchCancel:
                logger.info(f"Batch cancelled for user {user_id}")
                break
                
            except FloodWait as e:
                logger.warning(f"FloodWait detected: {e.value}s")
                await asyncio.sleep(e.value + 5)
                success = False
                
            except Exception as e:
                logger.error(f"Task error for user {user_id}: {e}")
                success = False
            
            # Update stats
            if success:
                saved += 1
            else:
                errors += 1
            
            # Complete task
            await queue_manager.complete_current_task(user_id, success)
            
            # Save state
            await db.save_queue_state(user_id, {
                "total_tasks": queue.total_tasks,
                "completed_tasks": queue.completed_tasks,
                "failed_tasks": queue.failed_tasks,
                "progress": queue.get_batch_progress(),
                "metadata": queue.metadata,
                "is_paused": queue.is_paused
            })
            
            # Wait between tasks
            await asyncio.sleep(WAITING_TIME)
        
        # Batch complete
        await show_completion_message(client, queue, saved, errors)
        
    except Exception as e:
        logger.error(f"Batch processing error for user {user_id}: {e}", exc_info=True)
        
    finally:
        batch_temp.IS_BATCH[user_id] = True
        await cleanup_user_client(acc)


async def get_user_client(user_id: int):
    """Get user client for downloads"""
    if LOGIN_SYSTEM:
        return await session_manager.get_session(user_id)
    else:
        return await session_manager.get_session(user_id)

async def handle_public_content(
    client: Client,
    acc: Optional[Client],
    source_id: str,
    task: FileTask,
    chat_id: int
) -> bool:
    """Handle public content download and forward to target chat"""
    user_id = task.from_user_id or chat_id
    target_chat = await get_target_chat(user_id, chat_id)

    try:
        # Fetch message using bot client first, fallback to user client if needed
        msg = None
        try:
            msg = await client.get_messages(source_id, task.msgid)
        except Exception as e:
            logger.debug(f"Bot client failed to get public message: {e}")
            if acc and getattr(acc, "is_connected", False):
                msg = await fetch_message_safe(acc, source_id, task.msgid)

        if not msg or msg.empty:
            if acc and getattr(acc, "is_connected", False):
                msg = await fetch_message_safe(acc, source_id, task.msgid)

        if not msg or msg.empty:
            logger.warning(f"Public message {task.msgid} in {source_id} is empty or not found")
            return False

        msg_type = get_message_type(msg)
        if not msg_type:
            logger.warning(f"Unsupported message type for message {task.msgid}, fallback to Document")
            msg_type = "Document"

        if msg_type == "Service":
            logger.info(f"Public message {task.msgid} is a service message, skipping")
            return True

        # Check user file filters
        if not await check_file_filters(user_id, msg_type, msg):
            logger.info(f"Public file {task.msgid} skipped by filter preferences")
            return True

        # Handle text messages
        if msg_type == "Text":
            return await handle_text_message(client, msg, target_chat, None)

        # Check if user has custom caption or thumbnail
        custom_caption = await db.get_caption(user_id)
        custom_thumb = await db.get_thumbnail(user_id)
        is_protected = getattr(msg, "has_protected_content", False)

        # If not protected and no custom caption/thumb, try direct copy_message to target_chat
        copied = False
        if not is_protected and not custom_caption and not custom_thumb:
            try:
                await client.copy_message(target_chat, msg.chat.id, msg.id)
                copied = True
            except Exception as e:
                logger.warning(f"copy_message to target_chat failed ({e}), falling back to download & upload for restricted content")
                copied = False

        if copied:
            # Update user stats
            file_size = 0
            for media_type in ["document", "video", "audio", "photo", "voice", "animation", "sticker"]:
                media = getattr(msg, media_type, None)
                if media and hasattr(media, "file_size"):
                    file_size = media.file_size
                    break

            await db.increment_user_stats(
                user_id,
                downloads=1,
                uploads=1,
                bandwidth=file_size
            )

            # Copy to global channel if enabled
            if ENABLE_GLOBAL_CHANNEL and GLOBAL_CHANNEL_ID and int(GLOBAL_CHANNEL_ID) != target_chat:
                try:
                    await client.copy_message(int(GLOBAL_CHANNEL_ID), msg.chat.id, msg.id)
                except Exception as e:
                    logger.error(f"Global channel copy error: {e}")

            return True

        # OPEN RESTRICTED CHANNEL or Custom Preferences -> Download and Upload to target_chat
        dl_client = acc if (acc and getattr(acc, "is_connected", False)) else client
        mock_msg = create_mock_message(chat_id, user_id)

        downloads_dir = get_downloads_dir()
        ensure_directory(downloads_dir)
        task_msg_id = getattr(task, 'message_id', 0) or 0
        task_msgid = getattr(task, 'msgid', task.msgid) or task.msgid
        raw_filename = generate_filename(msg, msg_type, task_msg_id, task_msgid)
        clean_filename = sanitize_filename(raw_filename)
        file_path = os.path.join(downloads_dir, clean_filename)

        if task:
            task.file_type = msg_type
            task.file_name = clean_filename
            task.file_path = file_path

        download_result = await download_restricted(
            dl_client, client, mock_msg, msg, task, user_id
        )

        # Retry if empty file
        if download_result.success and os.path.exists(download_result.file_path) and os.path.getsize(download_result.file_path) == 0:
            await safe_delete_file(download_result.file_path)
            alt_client = client if dl_client == acc else (acc if (acc and getattr(acc, "is_connected", False)) else None)
            if alt_client:
                download_result = await download_restricted(
                    alt_client, client, mock_msg, msg, task, user_id
                )

        if not download_result.success or not os.path.exists(download_result.file_path) or os.path.getsize(download_result.file_path) == 0:
            if download_result.file_path and os.path.exists(download_result.file_path):
                await safe_delete_file(download_result.file_path)
            
            # If error is because message doesn't contain downloadable media, send as text!
            if "downloadable media" in str(download_result.error).lower():
                logger.info(f"Public message {task.msgid} has no media file, sending as text message...")
                return await handle_text_message(client, msg, target_chat, None)

            if ERROR_MESSAGE:
                await client.send_message(
                    chat_id,
                    f"❌ **Download Error (Msg {task.msgid}):** {download_result.error or 'Failed to download file'}"
                )
            return False

        caption = await upload_service.get_user_caption(user_id, msg.caption)
        thumb_path = await upload_service.get_user_thumbnail(user_id, client)
        if not thumb_path:
            try:
                if msg_type == "Video" and msg.video and msg.video.thumbs:
                    thumb_path = await dl_client.download_media(msg.video.thumbs[0].file_id)
                elif msg_type == "Document" and msg.document and msg.document.thumbs:
                    thumb_path = await dl_client.download_media(msg.document.thumbs[0].file_id)
                elif msg_type == "Audio" and msg.audio and msg.audio.thumbs:
                    thumb_path = await dl_client.download_media(msg.audio.thumbs[0].file_id)
            except Exception as e:
                logger.warning(f"Failed to download original thumb: {e}")

        upload_result = await upload_service.upload_file(
            client,
            download_result.file_path,
            msg,
            msg_type,
            target_chat,
            None,
            caption,
            thumb_path,
            task,
            user_id
        )

        # Cleanup
        await safe_delete_file(download_result.file_path)
        if thumb_path:
            await safe_delete_file(thumb_path)

        if upload_result.success:
            await db.increment_user_stats(
                user_id,
                downloads=1,
                uploads=1,
                bandwidth=download_result.file_size
            )
            return True
        else:
            return False

    except FloodWait:
        raise
    except Exception as e:
        logger.error(f"Public content error for msg {task.msgid}: {e}", exc_info=True)
        return False


def create_mock_message(chat_id: int, user_id: int):
    """Create mock message for batch processing"""
    class MockMessage:
        def __init__(self, chat_id, user_id):
            self.chat = type('obj', (object,), {'id': chat_id})
            self.from_user = type('obj', (object,), {
                'id': user_id,
                'mention': f"User {user_id}",
                'first_name': f"User {user_id}"
            })
            self.id = None
            self.text = ""
            
    return MockMessage(chat_id, user_id)


async def cleanup_user_client(acc):
    """Cleanup user client (no-op for pooled sessions managed by session_manager)"""
    pass



async def show_completion_message(client: Client, queue, saved: int, errors: int):
    """Show batch completion message"""
    total_time = time.time() - (queue.batch_start_time or time.time())
    
    # Calculate statistics
    success_rate = ((queue.completed_tasks - queue.failed_tasks) / queue.total_tasks * 100) if queue.total_tasks > 0 else 0
    speed = (queue.total_tasks / total_time * 60) if total_time > 0 else 0
    
    completion_emoji = random.choice(["🎉", "✨", "✅", "🔥", "🌟", "🏆"])
    is_batch = queue.total_tasks > 1
    title = "BATCH PROCESSING COMPLETE!" if is_batch else "DOWNLOAD COMPLETE!"
    
    completion_text = f"""
{completion_emoji} **{title}** {completion_emoji}

━━━━━━━━━━━━━━━━━━━━
📊 **STATISTICS:**
├ 📁 Total Files: `{queue.total_tasks}`
├ ✅ Success: `{queue.completed_tasks - queue.failed_tasks}`
├ ❌ Failed: `{queue.failed_tasks}`
└ ⏱️ Time: `{time_formatter(total_time)}`

━━━━━━━━━━━━━━━━━━━━
🎯 **Success Rate:** `{success_rate:.1f}%`
⚡ **Avg Speed:** `{speed:.1f} files/min`

**Thank you for using the bot!** 🙏
"""
    
    try:
        await client.send_message(queue.chat_id, completion_text)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.send_message(queue.chat_id, completion_text)
    except Exception:
        pass
    
    # Cleanup progress message
    if queue.progress_message_id:
        try:
            success_text = """
✅ **OPERATION COMPLETE**

All tasks have been processed successfully.
Progress dashboard will close in 10 seconds...
"""
            await client.edit_message_text(
                chat_id=queue.chat_id,
                message_id=queue.progress_message_id,
                text=success_text
            )
            await asyncio.sleep(10)
            await client.delete_messages(queue.chat_id, queue.progress_message_id)
        except Exception:
            pass


# ============== PRIVATE & RESTRICTED CONTENT HANDLER ==============

async def fetch_message_safe(acc: Client, chat_id: Union[int, str], msg_id: int) -> Optional[Message]:
    """
    Fetch message from user client with automatic peer resolution and dialog fallback.
    Specifically handles private channels with strict protected content / noforwards.
    """
    # 1. Try direct get_messages
    try:
        msg = await acc.get_messages(chat_id, msg_id)
        if msg and not msg.empty:
            return msg
    except Exception as e:
        logger.debug(f"Direct get_messages failed for {chat_id}/{msg_id}: {e}")

    # 2. Try resolving chat via get_chat
    try:
        await acc.get_chat(chat_id)
        msg = await acc.get_messages(chat_id, msg_id)
        if msg and not msg.empty:
            return msg
    except Exception as e:
        logger.debug(f"get_chat failed for {chat_id}: {e}")

    # 3. Iterate dialogs to populate in-memory peer cache
    try:
        logger.info(f"Resolving private chat peer for {chat_id} via dialogs...")
        target_str = str(chat_id)
        clean_target_id = target_str.replace("-100", "").lstrip("-")
        
        async for dialog in acc.get_dialogs(limit=300):
            if not dialog.chat:
                continue
            d_id_str = str(dialog.chat.id)
            if d_id_str == target_str or d_id_str.replace("-100", "").lstrip("-") == clean_target_id:
                logger.info(f"Found and cached peer for {chat_id}: {dialog.chat.title or dialog.chat.id}")
                break
        
        msg = await acc.get_messages(chat_id, msg_id)
        if msg and not msg.empty:
            return msg
    except Exception as e:
        logger.error(f"Dialog search failed for {chat_id}: {e}")

    # 4. Try integer/channel variations if needed
    try:
        if isinstance(chat_id, str) and chat_id.startswith("-100"):
            alt_id = int(chat_id)
            msg = await acc.get_messages(alt_id, msg_id)
            if msg and not msg.empty:
                return msg
    except Exception:
        pass

    return None


async def download_restricted(acc, client, message, msg, task, user_id):
    """Helper to download restricted content using user client but updating progress with bot client"""
    start_time = time.time()
    try:
        last_update = 0
        async def progress(current, total):
            nonlocal last_update
            if task and task.status == TaskStatus.CANCELLED:
                raise pyrogram.StopTransmission
            
            now = time.time()
            if now - last_update < UPDATE_INTERVAL:
                return
            last_update = now

            # Update task
            if task:
                task.update_progress(current, total)
            queue_manager.update_task_progress(user_id, current, total, "download")
            
            # Update display using BOT client
            try:
                # Fix for MockMessage having None id causing AttributeError in progress_display
                prog_msg = message
                if getattr(message, "id", None) is None:
                    prog_msg = type("SafeMessage", (), {
                        "chat": message.chat,
                        "from_user": message.from_user,
                        "id": 1
                    })()
                
                await progress_callback(current, total, client, prog_msg, "download", user_id)
            except Exception:
                pass

        downloads_dir = get_downloads_dir()
        ensure_directory(downloads_dir)
        target_path = task.file_path if (task and task.file_path) else downloads_dir

        file_path = await acc.download_media(
            msg,
            file_name=target_path,
            progress=progress
        )
        
        if not file_path or not os.path.exists(file_path):
            return DownloadResult(False, error="Download failed - file not created")
            
        return DownloadResult(
            True,
            file_path=file_path,
            file_size=os.path.getsize(file_path),
            download_time=time.time() - start_time
        )
    except FileReferenceExpired:
        raise
    except FloodWait:
        raise
    except Exception as e:
        return DownloadResult(False, error=str(e))

async def handle_private(
    client: Client,
    acc: Client,
    message: Message,
    chatid: int,
    msgid: int,
    task: Optional[FileTask] = None
) -> bool:
    """Handle private/restricted content download with protected content support"""
    
    if task and task.status == TaskStatus.CANCELLED:
        return False
    
    # Retry loop for FileReferenceExpired and peer resolution
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Get message from user client using robust safe fetcher
            msg = await fetch_message_safe(acc, chatid, msgid)
            if not msg or msg.empty:
                logger.warning(f"Private message {msgid} in chat {chatid} not found (attempt {attempt+1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                if ERROR_MESSAGE:
                    await client.send_message(
                        message.chat.id,
                        f"❌ **Error:** Message `{msgid}` in chat `{chatid}` not accessible. Please ensure you have joined the channel with /login account."
                    )
                return False

            # Get message type
            msg_type = get_message_type(msg)
            if not msg_type:
                logger.warning(f"Unsupported message type for message {msgid}, fallback to Document")
                msg_type = "Document"

            if msg_type == "Service":
                logger.info(f"Message {msgid} is a service message, skipping")
                return True
            
            # Check file filters
            if not await check_file_filters(message.from_user.id, msg_type, msg):
                logger.info(f"Message {msgid} skipped by filter preferences")
                return True
            
            # Get target chat
            target_chat = await get_target_chat(message.from_user.id, message.chat.id)
            
            # Ensure downloads directory and sanitize filename
            downloads_dir = get_downloads_dir()
            ensure_directory(downloads_dir)
            task_msg_id = getattr(task, 'message_id', 0) or 0
            task_msgid = getattr(task, 'msgid', msgid) or msgid
            raw_filename = generate_filename(msg, msg_type, task_msg_id, task_msgid)
            clean_filename = sanitize_filename(raw_filename)
            file_path = os.path.join(downloads_dir, clean_filename)

            # Update task info
            if task:
                task.file_type = msg_type
                task.file_name = clean_filename
                task.file_path = file_path
            
            # Handle text messages
            if msg_type == "Text":
                reply_to = message.id if target_chat == message.chat.id else None
                return await handle_text_message(client, msg, target_chat, reply_to)
            
            # Download file
            download_result = await download_restricted(
                acc, client, message, msg, task, message.from_user.id
            )
            
            # Check for empty file (symptom of failed download due to FileReferenceExpired)
            if download_result.success and os.path.exists(download_result.file_path) and os.path.getsize(download_result.file_path) == 0:
                logger.warning(f"Download resulted in empty file (attempt {attempt+1}/{max_retries}). Retrying...")
                await safe_delete_file(download_result.file_path)
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                else:
                    if ERROR_MESSAGE:
                        await client.send_message(
                            message.chat.id,
                            "❌ **Download Error:** File is empty after retries."
                        )
                    return False

            if not download_result.success:
                # If error is because message doesn't contain downloadable media, send as text!
                if "downloadable media" in str(download_result.error).lower():
                    logger.info(f"Message {msgid} has no media file, sending as text message...")
                    reply_to = message.id if target_chat == message.chat.id else None
                    return await handle_text_message(client, msg, target_chat, reply_to)

                if ERROR_MESSAGE:
                    await client.send_message(
                        message.chat.id,
                        f"❌ **Download Error (Msg {msgid}):** {download_result.error}"
                    )
                return False
            
            # Get user caption and thumbnail
            caption = await upload_service.get_user_caption(
                message.from_user.id,
                msg.caption
            )
            
            thumb_path = await upload_service.get_user_thumbnail(
                message.from_user.id,
                client
            )
            
            if not thumb_path:
                try:
                    if msg_type in ["Video", "VideoNote"] and msg.video and msg.video.thumbs:
                        thumb_path = await acc.download_media(msg.video.thumbs[0].file_id)
                    elif msg_type == "Document" and msg.document and msg.document.thumbs:
                        thumb_path = await acc.download_media(msg.document.thumbs[0].file_id)
                    elif msg_type == "Audio" and msg.audio and msg.audio.thumbs:
                        thumb_path = await acc.download_media(msg.audio.thumbs[0].file_id)
                except Exception as e:
                    logger.warning(f"Failed to download original thumbnail: {e}")
            
            # Upload file
            reply_to = message.id if target_chat == message.chat.id else None
            upload_result = await upload_service.upload_file(
                client,
                download_result.file_path,
                msg,
                msg_type,
                target_chat,
                reply_to,
                caption,
                thumb_path,
                task,
                message.from_user.id
            )
            
            # Cleanup
            await safe_delete_file(download_result.file_path)
            if thumb_path:
                await safe_delete_file(thumb_path)
            
            # Update user stats
            if upload_result.success:
                await db.increment_user_stats(
                    message.from_user.id,
                    downloads=1,
                    uploads=1,
                    bandwidth=download_result.file_size
                )
                
                # Copy to global channel if enabled
                if ENABLE_GLOBAL_CHANNEL and GLOBAL_CHANNEL_ID:
                    try:
                        await upload_service.upload_file(
                            client,
                            download_result.file_path,
                            msg,
                            msg_type,
                            int(GLOBAL_CHANNEL_ID),
                            None,
                            caption,
                            None,
                            None,
                            None
                        )
                    except Exception as e:
                        logger.error(f"Global channel upload error: {e}")
            
                return True
            else:
                return False
            
        except FloodWait as e:
            logger.warning(f"FloodWait detected: {e.value}s")
            await asyncio.sleep(e.value + 5)
            continue
            
        except FileReferenceExpired:
            logger.warning(f"FileReferenceExpired encountered (attempt {attempt+1}/{max_retries}). Retrying...")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            else:
                logger.error("FileReferenceExpired persisted after retries.")
                if ERROR_MESSAGE:
                    await client.send_message(
                        message.chat.id,
                        "❌ **Error:** File reference expired and could not be refreshed."
                    )
                return False
                
        except BatchCancel:
            return False
            
        except Exception as e:
            logger.error(f"Private content error for msg {msgid}: {e}", exc_info=True)
            if attempt == max_retries - 1 and ERROR_MESSAGE:
                await client.send_message(
                    message.chat.id,
                    f"❌ **Error (Msg {msgid}):** {str(e)[:200]}"
                )
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            return False
            
    return False


def get_message_type(msg: Message) -> str:
    """Get message type with accurate text and media detection"""
    if not msg or getattr(msg, "empty", False):
        return "Unknown"
    
    if getattr(msg, "service", False):
        return "Service"
    
    if getattr(msg, "document", None):
        return "Document"
    elif getattr(msg, "video", None):
        return "Video"
    elif getattr(msg, "video_note", None):
        return "VideoNote"
    elif getattr(msg, "animation", None):
        return "Animation"
    elif getattr(msg, "sticker", None):
        return "Sticker"
    elif getattr(msg, "voice", None):
        return "Voice"
    elif getattr(msg, "audio", None):
        return "Audio"
    elif getattr(msg, "photo", None):
        return "Photo"
    
    media = getattr(msg, "media", None)
    if media:
        media_str = str(media).upper()
        if "PHOTO" in media_str:
            return "Photo"
        elif "VIDEO_NOTE" in media_str:
            return "VideoNote"
        elif "VIDEO" in media_str:
            return "Video"
        elif "AUDIO" in media_str:
            return "Audio"
        elif "VOICE" in media_str:
            return "Voice"
        elif "ANIMATION" in media_str:
            return "Animation"
        elif "STICKER" in media_str:
            return "Sticker"
        elif "DOCUMENT" in media_str:
            return "Document"
    
    # If not any downloadable media, it's Text
    return "Text"


async def check_file_filters(user_id: int, msg_type: str, msg: Message) -> bool:
    """Check if file type is allowed by user filters"""
    try:
        filters_dict = await db.get_file_preferences(user_id)
        if not filters_dict:
            return True
        
        if msg_type == "Document":
            file_name = getattr(msg.document, "file_name", "")
            if file_name and file_name.lower().endswith((".zip", ".rar", ".7z", ".tar", ".gz")):
                return filters_dict.get("zip", True)
            return filters_dict.get("document", True)
        elif msg_type in ["Video", "VideoNote"]:
            return filters_dict.get("video", True)
        elif msg_type == "Audio":
            return filters_dict.get("audio", True)
        elif msg_type == "Photo":
            return filters_dict.get("photo", True)
        elif msg_type == "Animation":
            return filters_dict.get("animation", True)
        elif msg_type == "Sticker":
            return filters_dict.get("sticker", True)
        elif msg_type == "Voice":
            return filters_dict.get("voice", True)
        
        return True
        
    except Exception:
        return True


async def get_target_chat(user_id: int, default_chat_id: int) -> int:
    """Get target chat for uploads"""
    chat_id = await db.get_chat_id(user_id)
    if chat_id:
        return int(chat_id)
    
    if CHANNEL_ID:
        try:
            return int(CHANNEL_ID)
        except:
            return default_chat_id
    
    return default_chat_id


async def handle_text_message(client: Client, msg: Message, target_chat: int, reply_id: int) -> bool:
    """Handle text message forwarding"""
    try:
        text = msg.text or msg.caption or (msg.web_page.url if getattr(msg, "web_page", None) else "")
        if not text:
            logger.warning("Empty text message, skipping")
            return True
        entities = msg.entities or msg.caption_entities
        logger.info(f"Sending text message ({len(text)} chars) to target_chat {target_chat} (reply_id={reply_id})...")
        sent_msg = await client.send_message(
            target_chat,
            text,
            entities=entities,
            reply_to_message_id=reply_id
        )
        logger.info(f"✅ Text message successfully sent to target_chat {target_chat} (msg_id={sent_msg.id})")
        return True
    except Exception as e:
        logger.error(f"Text message error sending to target_chat {target_chat}: {e}")
        return False


def generate_filename(msg: Message, msg_type: str, task_id: int, msgid: int) -> str:
    """Generate sanitized filename for download"""
    media = None
    if msg_type not in ["Text", "VideoNote"]:
        media = getattr(msg, msg_type.lower(), None)
    elif msg_type == "VideoNote":
        media = getattr(msg, "video_note", None)
    
    # Try to get original filename
    if media and hasattr(media, 'file_name') and media.file_name:
        return sanitize_filename(media.file_name)
    
    # Generate filename based on type
    timestamp = int(time.time())
    
    if msg_type == "Text":
        return f"text_message_{task_id}_{msgid}_{timestamp}.txt"
    elif msg_type == "Photo":
        return f"photo_{task_id}_{msgid}_{timestamp}.jpg"
    elif msg_type == "Video":
        mime = getattr(media, "mime_type", "") if media else ""
        if "x-matroska" in mime:
            return f"video_{task_id}_{msgid}_{timestamp}.mkv"
        elif "webm" in mime:
            return f"video_{task_id}_{msgid}_{timestamp}.webm"
        return f"video_{task_id}_{msgid}_{timestamp}.mp4"
    elif msg_type == "VideoNote":
        return f"videonote_{task_id}_{msgid}_{timestamp}.mp4"
    elif msg_type == "Audio":
        mime = getattr(media, "mime_type", "") if media else ""
        if "ogg" in mime:
            return f"audio_{task_id}_{msgid}_{timestamp}.ogg"
        elif "wav" in mime:
            return f"audio_{task_id}_{msgid}_{timestamp}.wav"
        return f"audio_{task_id}_{msgid}_{timestamp}.mp3"
    elif msg_type == "Voice":
        return f"voice_{task_id}_{msgid}_{timestamp}.ogg"
    elif msg_type == "Animation":
        mime = getattr(media, "mime_type", "") if media else ""
        if "gif" in mime:
            return f"animation_{task_id}_{msgid}_{timestamp}.gif"
        return f"animation_{task_id}_{msgid}_{timestamp}.mp4"
    elif msg_type == "Sticker":
        if media:
            if getattr(media, "is_animated", False):
                return f"sticker_{task_id}_{msgid}_{timestamp}.tgs"
            elif getattr(media, "is_video", False):
                return f"sticker_{task_id}_{msgid}_{timestamp}.webm"
        return f"sticker_{task_id}_{msgid}_{timestamp}.webp"
    elif msg_type == "Document":
        return f"document_{task_id}_{msgid}_{timestamp}.bin"
    
    return f"file_{task_id}_{msgid}_{timestamp}.bin"