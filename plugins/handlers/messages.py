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
    PasswordHashInvalid, AuthKeyDuplicated, FileReferenceExpired,
    ChannelInvalid, ChannelPrivate, PeerIdInvalid
)
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType

from config import (
    LOGIN_SYSTEM, STRING_SESSION, ERROR_MESSAGE, WAITING_TIME,
    CHANNEL_ID, ENABLE_GLOBAL_CHANNEL, GLOBAL_CHANNEL_ID,
    MAX_BATCH_SIZE, DOWNLOAD_TIMEOUT, UPLOAD_TIMEOUT, ADMINS,
    LOG_CHANNEL, UPDATE_INTERVAL,
    SESSION_DEVICE_MODEL, SESSION_APP_VERSION, SESSION_SYSTEM_VERSION
)

from database.mongodb import db
from plugins.security.auth import auth_manager, is_authorized
from plugins.security.encryption import encrypt_data
from plugins.core.utils import (
    get_logger, humanbytes, time_formatter, get_ist_time,
    truncate_text, rate_limiter, get_downloads_dir,
    safe_delete_file, validate_telegram_link, ensure_directory,
    sanitize_filename, get_extension_from_mime, is_archive_filename
)
from plugins.core.models import FileTask, TaskStatus, BatchCancel, LinkInfo, DownloadResult
from plugins.core.animations import ProgressAnimations
from plugins.services.queue_manager import queue_manager
from plugins.services.session_manager import session_manager
from plugins.services.downloader import download_service
from plugins.services.uploader import upload_service
from plugins.progress_display import progress_callback, update_progress_display
from plugins.monitoring.metrics import usage_stats
from plugins.handlers.callbacks import handle_progress_controls

logger = get_logger(__name__)

# Patch Pyrogram to support newer channel IDs
utils.MIN_CHANNEL_ID = -100999999999999


# Global variables for batch processing
batch_temp = type('BatchTemp', (), {'IS_BATCH': {}})()
user_states: Dict[int, Dict[str, Any]] = {}


def get_media_file_size(msg: Message) -> int:
    """Calculate file size for message across all media types"""
    for media_type in ["document", "video", "video_note", "audio", "photo", "voice", "animation", "sticker"]:
        media = getattr(msg, media_type, None)
        if media and hasattr(media, "file_size") and media.file_size:
            return media.file_size
    return 0


async def download_original_thumb(client_to_use: Optional[Client], msg: Message, msg_type: str) -> Optional[str]:
    """Safely download original thumbnail for media if available"""
    if not client_to_use:
        return None
    try:
        if msg_type in ["Video", "VideoNote"]:
            vid = getattr(msg, "video", None) or getattr(msg, "video_note", None)
            if vid and getattr(vid, "thumbs", None):
                return await client_to_use.download_media(vid.thumbs[0].file_id)
        elif msg_type == "Document" and getattr(msg, "document", None) and getattr(msg.document, "thumbs", None):
            return await client_to_use.download_media(msg.document.thumbs[0].file_id)
        elif msg_type == "Audio" and getattr(msg, "audio", None) and getattr(msg.audio, "thumbs", None):
            return await client_to_use.download_media(msg.audio.thumbs[0].file_id)
        elif msg_type == "Animation" and getattr(msg, "animation", None) and getattr(msg.animation, "thumbs", None):
            return await client_to_use.download_media(msg.animation.thumbs[0].file_id)
    except Exception as e:
        logger.debug(f"Failed to download original thumbnail: {e}")
    return None


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
    """Handle all private text messages, direct media, and forwarded content"""
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
    
    # Check text links
    if message.text:
        # Handle join chat links
        if "https://t.me/+" in message.text or "https://t.me/joinchat/" in message.text:
            await handle_join_chat(client, message)
            return
        
        # Handle download links
        if "https://t.me/" in message.text:
            await handle_download_link(client, message)
            return
    
    # Check for direct media or forwarded content
    has_media = bool(
        message.document or message.video or message.video_note or
        message.audio or message.voice or message.photo or
        message.animation or message.sticker or message.poll or
        message.location or message.venue or message.contact or
        message.dice or message.forward_date
    )
    
    if has_media:
        await handle_direct_media(client, message)
        return
    
    # Handle normal text messages
    await message.reply_text(
        "❓ **Unknown command**\n\n"
        "Send a Telegram link or forward any media (videos, audio, documents, photos, animations, stickers, voice, archives) to process.\n"
        "Use /help for more information."
    )


async def handle_direct_media(client: Client, message: Message):
    """Handle direct media uploads or forwarded messages sent to the bot"""
    user_id = message.from_user.id
    target_chat = await get_target_chat(user_id, message.chat.id)
    msg_type = get_message_type(message)
    
    # Check user file filters
    if not await check_file_filters(user_id, msg_type, message):
        await message.reply_text(f"⚠️ **{msg_type}** is disabled in your file filter settings.")
        return
    
    # If target chat is configured and different from bot DM, forward/copy directly if possible
    copied = False
    if target_chat != message.chat.id:
        try:
            await client.copy_message(target_chat, message.chat.id, message.id)
            copied = True
        except Exception:
            try:
                await client.forward_messages(target_chat, message.chat.id, message.id)
                copied = True
            except Exception:
                pass
    
    if copied:
        file_size = get_media_file_size(message)
        await db.increment_user_stats(user_id, downloads=1, uploads=1, bandwidth=file_size)
        await message.reply_text(f"✅ **Media successfully forwarded to target chat!**\n🆔 `{target_chat}`")
        return

    # If direct copy was not possible or target is current chat, download and upload with custom caption/thumbnail
    status_msg = await message.reply_text("🔄 **Processing media...**")
    try:
        downloads_dir = get_downloads_dir()
        ensure_directory(downloads_dir)
        raw_filename = generate_filename(message, msg_type, message.id, message.id)
        clean_filename = sanitize_filename(raw_filename)
        file_path = os.path.join(downloads_dir, clean_filename)
        
        dl_res = await download_service.download_media(client, message, message, None, user_id)
        if not dl_res.success or not dl_res.file_path or not os.path.exists(dl_res.file_path):
            await status_msg.edit_text(f"❌ **Download Error:** {dl_res.error or 'Failed to download media'}")
            return
        
        caption = await upload_service.get_user_caption(user_id, message.caption)
        thumb_path = await upload_service.get_user_thumbnail(user_id, client)
        if not thumb_path:
            thumb_path = await download_original_thumb(client, message, msg_type)
        
        dest_chat = target_chat if target_chat != message.chat.id else message.chat.id
        reply_to = None if dest_chat != message.chat.id else message.id
        
        up_res = await upload_service.upload_file(
            client,
            dl_res.file_path,
            message,
            msg_type,
            dest_chat,
            reply_to,
            caption,
            thumb_path,
            None,
            user_id
        )
        
        await safe_delete_file(dl_res.file_path)
        if thumb_path:
            await safe_delete_file(thumb_path)
            
        if up_res.success:
            if dest_chat != message.chat.id:
                await status_msg.edit_text(f"✅ **Sent to target chat!**\n🆔 `{dest_chat}`")
            else:
                await status_msg.delete()
            await db.increment_user_stats(user_id, downloads=1, uploads=1, bandwidth=dl_res.file_size)
        else:
            await status_msg.edit_text(f"❌ **Upload error:** {up_res.error or 'Failed to upload'}")
            
    except Exception as e:
        logger.error(f"Error handling direct media: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ **Error:** {str(e)[:200]}")


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
            device_model=SESSION_DEVICE_MODEL,
            app_version=SESSION_APP_VERSION,
            system_version=SESSION_SYSTEM_VERSION,
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
    """Handle target chat setting via forward, chat ID, username, or link"""
    user_id = message.from_user.id
    
    if message.text and message.text.strip() == "/cancel":
        user_states.pop(user_id, None)
        await message.reply_text("❌ Target chat setting cancelled.")
        return
    
    chat_id = None
    chat_name = None

    # 1. Check if it's a forwarded message
    if message.forward_from_chat:
        chat = message.forward_from_chat
        chat_id = chat.id
        chat_name = chat.title or f"Chat {chat_id}"
    elif message.text:
        text = message.text.strip()
        
        # Check if user sent integer chat ID like -100123456789 or 123456789
        try:
            potential_id = int(text)
            chat_id = potential_id
        except ValueError:
            # Check if it's a link or username
            if "t.me/" in text:
                parts = text.split("?")[0].strip().split("/")
                clean_parts = [p for p in parts if p]
                if "c" in clean_parts:
                    c_idx = clean_parts.index("c")
                    if len(clean_parts) > c_idx + 1:
                        try:
                            chat_id = int(f"-100{clean_parts[c_idx + 1]}")
                        except ValueError:
                            pass
                elif clean_parts:
                    chat_id = clean_parts[-1]
            elif text.startswith("@"):
                chat_id = text
            else:
                chat_id = text

    if not chat_id:
        await message.reply_text(
            "❌ **Could not detect target chat**\n\n"
            "Please provide one of the following:\n"
            "1. **Forward a message** from your target channel/group\n"
            "2. Send the **Channel ID** (e.g. `-100123456789`)\n"
            "3. Send the **Channel Username** (e.g. `@mychannel`)\n"
            "4. Send the **Channel Link** (e.g. `https://t.me/mychannel`)\n\n"
            "Or send /cancel to abort."
        )
        return

    # Try resolving chat info to ensure bot has access or to get title
    bot_has_access = False
    bot_me = None
    try:
        bot_me = await client.get_me()
        chat_obj = await client.get_chat(chat_id)
        if chat_obj:
            chat_id = chat_obj.id
            chat_name = chat_obj.title or chat_obj.username or f"Chat {chat_id}"
            bot_has_access = True
    except Exception as e:
        logger.warning(f"Could not fetch chat info for {chat_id} using bot client: {e}")
        # Try user client if available
        acc = await get_user_client(user_id)
        if acc and getattr(acc, "is_connected", False):
            try:
                chat_obj = await acc.get_chat(chat_id)
                if chat_obj:
                    chat_id = chat_obj.id
                    chat_name = chat_obj.title or chat_obj.username or f"Chat {chat_id}"
            except Exception:
                pass
        
        if isinstance(chat_id, str) and not chat_name:
            chat_name = chat_id

    # Save chat ID
    await db.save_preferences(user_id, target_chat_id=chat_id)
    user_states.pop(user_id, None)

    bot_username = f"@{bot_me.username}" if bot_me and bot_me.username else "the bot"

    if bot_has_access:
        await message.reply_text(
            f"✅ **Target Chat Saved!**\n\n"
            f"All files will be sent to:\n"
            f"📢 **{chat_name or chat_id}**\n"
            f"🆔 `{chat_id}`"
        )
    else:
        await message.reply_text(
            f"✅ **Target Chat Saved:** `{chat_id}`\n\n"
            f"⚠️ **IMPORTANT NOTICE:**\n"
            f"Bot is **NOT yet an Administrator** in **{chat_name or chat_id}**!\n\n"
            f"👉 **Files receive karne ke liye:**\n"
            f"1. Target channel/group open karein\n"
            f"2. Channel settings ➔ **Administrators** ➔ **Add Administrator**\n"
            f"3. `{bot_username}` ko add karein aur **'Post Messages'** permission dein.\n\n"
            f"Jab tak bot admin nahi banega, Telegram bot ko channel me files bhejne ki permission nahi dega."
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
            
            # Initial progress display update when task starts
            await update_progress_display(client, user_id, force=True)
            
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
            
            # Update progress display after completing task
            await update_progress_display(client, user_id, force=True)
            
            # Save state
            await db.save_queue_state(user_id, {
                "total_tasks": queue.total_tasks,
                "completed_tasks": queue.completed_tasks,
                "failed_tasks": queue.failed_tasks,
                "progress": queue.get_batch_progress(),
                "metadata": queue.metadata,
                "is_paused": queue.is_paused
            })
            
            # Wait between tasks (minimum 1 second to avoid FloodWait)
            await asyncio.sleep(max(1, WAITING_TIME))
        
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
    """Handle public content: direct forward if allowed (1s delay), or download & upload if restricted"""
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

        task_msg_id = getattr(task, 'message_id', 0) or 0
        task_msgid = getattr(task, 'msgid', task.msgid) or task.msgid
        raw_filename = generate_filename(msg, msg_type, task_msg_id, task_msgid)
        clean_filename = sanitize_filename(raw_filename)
        file_size = get_media_file_size(msg)

        if task:
            task.file_type = msg_type
            task.file_name = clean_filename
            task.size = file_size

        if msg_type == "Service":
            logger.info(f"Public message {task.msgid} is a service message, skipping")
            if task:
                task.status = TaskStatus.SKIPPED
            return True

        # Check user file filters
        if not await check_file_filters(user_id, msg_type, msg):
            logger.info(f"Public file {task.msgid} skipped by filter preferences")
            if task:
                task.status = TaskStatus.SKIPPED
            return True

        # Handle text messages
        if msg_type == "Text":
            if task:
                task.status = TaskStatus.FORWARDING
                task.progress = 0.0
                await update_progress_display(client, user_id, force=True)
            res = await handle_text_message(client, msg, target_chat, None)
            if task and res:
                task.progress = 100.0
                await update_progress_display(client, user_id, force=True)
            await asyncio.sleep(1)
            return res

        # Check if forwarding is allowed or if content is restricted
        is_protected = getattr(msg, "has_protected_content", False) or getattr(getattr(msg, "chat", None), "has_protected_content", False)
        user_queue = queue_manager.get_queue(user_id)

        # IF FORWARDING ALLOWED: Forward/Copy directly without download or upload
        copied = False
        if not is_protected:
            if user_queue:
                user_queue.is_forwarding_mode = True
            if task:
                task.status = TaskStatus.FORWARDING
                task.progress = 0.0
                await update_progress_display(client, user_id, force=True)

            try:
                # 1. Try bot copy_message
                await client.copy_message(target_chat, msg.chat.id, msg.id)
                copied = True
            except Exception as e:
                logger.debug(f"client.copy_message failed ({e}), trying client.forward_messages...")
                try:
                    await client.forward_messages(target_chat, msg.chat.id, msg.id)
                    copied = True
                except Exception as e2:
                    logger.debug(f"client.forward_messages failed ({e2})")
                    if acc and getattr(acc, "is_connected", False):
                        try:
                            await acc.copy_message(target_chat, msg.chat.id, msg.id)
                            copied = True
                        except Exception:
                            try:
                                await acc.forward_messages(target_chat, msg.chat.id, msg.id)
                                copied = True
                            except Exception:
                                pass
                    if not copied and str(target_chat) != str(user_id):
                        try:
                            await client.copy_message(user_id, msg.chat.id, msg.id)
                            copied = True
                        except Exception:
                            try:
                                await client.forward_messages(user_id, msg.chat.id, msg.id)
                                copied = True
                            except Exception:
                                pass

        if copied:
            if task:
                task.progress = 100.0
                task.status = TaskStatus.FORWARDING
                await update_progress_display(client, user_id, force=True)

            # Update user stats
            file_size = get_media_file_size(msg)
            await db.increment_user_stats(
                user_id,
                downloads=1,
                uploads=1,
                bandwidth=file_size
            )

            # Copy to global channel if enabled
            if ENABLE_GLOBAL_CHANNEL and GLOBAL_CHANNEL_ID:
                try:
                    clean_global = int(str(GLOBAL_CHANNEL_ID).split("#")[0].strip())
                    if clean_global != target_chat:
                        await client.copy_message(clean_global, msg.chat.id, msg.id)
                except Exception as e:
                    logger.error(f"Global channel copy error: {e}")

            # 1 second delay between files to avoid FloodWait
            await asyncio.sleep(1)
            return True

        # RESTRICTED CONTENT (or copy failed due to restrictions): Download and Upload to target_chat
        logger.info(f"Handling restricted public message {task.msgid} via download & upload to target_chat {target_chat}...")
        if user_queue:
            user_queue.is_forwarding_mode = False
        if task:
            task.status = TaskStatus.DOWNLOADING
            task.progress = 0.0
            await update_progress_display(client, user_id, force=True)
        dl_client = acc if (acc and getattr(acc, "is_connected", False)) else client
        mock_msg = create_mock_message(chat_id, user_id)

        downloads_dir = get_downloads_dir()
        ensure_directory(downloads_dir)
        file_path = os.path.join(downloads_dir, clean_filename)

        if task:
            task.file_type = msg_type
            task.file_name = clean_filename
            task.file_path = file_path
            task.status = TaskStatus.DOWNLOADING

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
                res = await handle_text_message(client, msg, target_chat, None)
                await asyncio.sleep(1)
                return res

            if ERROR_MESSAGE:
                await client.send_message(
                    chat_id,
                    f"❌ **Download Error (Msg {task.msgid}):** {download_result.error or 'Failed to download file'}"
                )
            return False

        caption = await upload_service.get_user_caption(user_id, msg.caption)
        thumb_path = await upload_service.get_user_thumbnail(user_id, client)
        if not thumb_path:
            thumb_path = await download_original_thumb(dl_client, msg, msg_type)

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
            # 1-second delay between files
            await asyncio.sleep(1)
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
            self.id = 1
            self.text = ""
            
    return MockMessage(chat_id, user_id)


async def show_completion_message(client: Client, queue, saved: int, errors: int):
    """Show rich batch completion message with download statistics"""
    try:
        total_time = 0
        if queue.batch_start_time:
            total_time = time.time() - queue.batch_start_time

        success_rate = (saved / (saved + errors) * 100) if (saved + errors) > 0 else 100

        source_info = ""
        meta = queue.metadata or {}
        if meta.get("type") == "private":
            source_info = f"\n🔒 **Source:** Private Channel `{meta.get('source_id', '')}`"
        elif meta.get("type") == "public":
            source_info = f"\n📢 **Source:** Public Channel `{meta.get('source_id', '')}`"

        completion_text = f"""
🎉 **Batch Download Completed!**

━━━━━━━━━━━━━━━━━━━━
✅ **Successfully Saved:** `{saved}` files
❌ **Errors/Skipped:** `{errors}` files
📊 **Success Rate:** `{success_rate:.1f}%`
⏱️ **Total Time:** `{time_formatter(total_time)}`{source_info}
━━━━━━━━━━━━━━━━━━━━
✨ All files have been forwarded to your destination chat.
"""
        # Try editing the existing progress message, or send a new one
        if queue.progress_message_id and queue.chat_id:
            try:
                await client.edit_message_text(
                    chat_id=queue.chat_id,
                    message_id=queue.progress_message_id,
                    text=completion_text,
                    parse_mode=enums.ParseMode.MARKDOWN
                )
                return
            except Exception as edit_err:
                logger.debug(f"Could not edit progress message on completion: {edit_err}")

        # If edit failed or progress message wasn't tracked, send fresh message
        if queue.chat_id:
            await client.send_message(queue.chat_id, completion_text)
    except Exception as e:
        logger.error(f"Error showing completion message: {e}")
        if queue.chat_id:
            try:
                await client.send_message(queue.chat_id, f"✅ **Batch Complete!** Saved: {saved}, Errors: {errors}")
            except Exception:
                pass


async def cleanup_user_client(acc):
    """Safely stop temporary user client if needed"""
    if not acc:
        return
    try:
        # Don't stop global string session if running continuously
        if not STRING_SESSION:
            if getattr(acc, "is_connected", False):
                await acc.stop()
    except Exception as e:
        logger.warning(f"Error stopping user client: {e}")


# ============== PRIVATE & RESTRICTED CONTENT HANDLER ==============

async def fetch_message_safe(acc: Client, chat_id: Union[int, str], msg_id: int) -> Optional[Message]:
    """Robust message fetcher that resolves channel access via dialogs caching"""
    if not acc:
        return None
    
    # 1. Direct fetch attempt
    try:
        msg = await acc.get_messages(chat_id, msg_id)
        if msg and not msg.empty:
            return msg
    except (ChannelInvalid, ChannelPrivate, PeerIdInvalid, KeyError) as e:
        logger.debug(f"Direct fetch failed for {chat_id}/{msg_id} ({e}), trying peer resolution...")
    except Exception as e:
        logger.debug(f"Direct fetch exception: {e}")

    # 2. Try fetching chat entity to populate internal peer cache
    try:
        chat_obj = await acc.get_chat(chat_id)
        if chat_obj:
            msg = await acc.get_messages(chat_obj.id, msg_id)
            if msg and not msg.empty:
                return msg
    except Exception as e:
        logger.debug(f"get_chat resolution failed: {e}")

    # 3. Dialog search fallback: iterate through user's active dialogs to populate peer cache
    try:
        clean_target_id = str(chat_id).replace("-100", "").lstrip("-")
        target_str = str(chat_id)
        
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
                if task:
                    task.status = TaskStatus.SKIPPED
                return True
            
            # Check file filters
            if not await check_file_filters(message.from_user.id, msg_type, msg):
                logger.info(f"Message {msgid} skipped by filter preferences")
                if task:
                    task.status = TaskStatus.SKIPPED
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
            file_size = get_media_file_size(msg)

            # Update task info
            if task:
                task.file_type = msg_type
                task.file_name = clean_filename
                task.file_path = file_path
                task.size = file_size
            
            # Handle text messages
            if msg_type == "Text":
                reply_to = message.id if target_chat == message.chat.id else None
                if task:
                    task.status = TaskStatus.FORWARDING
                    task.progress = 0.0
                    await update_progress_display(client, message.from_user.id, force=True)
                res = await handle_text_message(client, msg, target_chat, reply_to)
                if task and res:
                    task.progress = 100.0
                    await update_progress_display(client, message.from_user.id, force=True)
                await asyncio.sleep(1)
                return res

            # Check if forwarding is allowed or if content is restricted
            is_protected = getattr(msg, "has_protected_content", False) or getattr(getattr(msg, "chat", None), "has_protected_content", False)
            user_queue = queue_manager.get_queue(message.from_user.id)

            # IF FORWARDING ALLOWED: Forward/Copy directly without download or upload
            copied = False
            if not is_protected:
                if user_queue:
                    user_queue.is_forwarding_mode = True
                if task:
                    task.status = TaskStatus.FORWARDING
                    task.progress = 0.0
                    await update_progress_display(client, message.from_user.id, force=True)

                # 1. Try direct copy or forward from user client `acc` to `target_chat`
                try:
                    await acc.copy_message(target_chat, chatid, msgid)
                    copied = True
                except Exception as e1:
                    logger.debug(f"acc.copy_message to {target_chat} failed: {e1}")
                    try:
                        await acc.forward_messages(target_chat, chatid, msgid)
                        copied = True
                    except Exception as e2:
                        logger.debug(f"acc.forward_messages to {target_chat} failed: {e2}")

                # 2. If acc cannot directly post to target_chat (e.g. only bot is admin in target_chat),
                # relay via bot DM: acc forwards to bot, bot copies/forwards to target_chat
                if not copied:
                    try:
                        bot_me = await client.get_me()
                        fwd = await acc.forward_messages(bot_me.id, chatid, msgid)
                        if fwd:
                            fwd_msg = fwd[0] if isinstance(fwd, list) else fwd
                            try:
                                await client.copy_message(target_chat, fwd_msg.chat.id, fwd_msg.id)
                                copied = True
                            except Exception:
                                try:
                                    await client.forward_messages(target_chat, fwd_msg.chat.id, fwd_msg.id)
                                    copied = True
                                except Exception:
                                    pass
                            if not copied and str(target_chat) != str(message.from_user.id):
                                try:
                                    await client.copy_message(message.from_user.id, fwd_msg.chat.id, fwd_msg.id)
                                    copied = True
                                except Exception:
                                    try:
                                        await client.forward_messages(message.from_user.id, fwd_msg.chat.id, fwd_msg.id)
                                        copied = True
                                    except Exception:
                                        pass
                            try:
                                await fwd_msg.delete()
                            except Exception:
                                pass
                    except Exception as e_relay:
                        logger.debug(f"Relay forwarding for private chat {chatid}/{msgid} failed: {e_relay}")

            if copied:
                if task:
                    task.progress = 100.0
                    task.status = TaskStatus.FORWARDING
                    await update_progress_display(client, message.from_user.id, force=True)

                # Calculate file size for stats
                file_size = get_media_file_size(msg)
                await db.increment_user_stats(
                    message.from_user.id,
                    downloads=1,
                    uploads=1,
                    bandwidth=file_size
                )

                # Copy to global channel if enabled
                if ENABLE_GLOBAL_CHANNEL and GLOBAL_CHANNEL_ID:
                    try:
                        clean_global = int(str(GLOBAL_CHANNEL_ID).split("#")[0].strip())
                        if clean_global != target_chat:
                            await client.copy_message(clean_global, chatid, msgid)
                    except Exception as e:
                        logger.error(f"Global channel copy error: {e}")

                # 1 second delay between files to avoid FloodWait
                await asyncio.sleep(1)
                return True

            # RESTRICTED CONTENT (or copy failed due to restriction): Download and Upload to target_chat
            logger.info(f"Handling restricted private message {msgid} via download & upload to target_chat {target_chat}...")
            if user_queue:
                user_queue.is_forwarding_mode = False
            if task:
                task.status = TaskStatus.DOWNLOADING
                task.progress = 0.0
                await update_progress_display(client, message.from_user.id, force=True)
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
                    res = await handle_text_message(client, msg, target_chat, reply_to)
                    await asyncio.sleep(1)
                    return res

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
                thumb_path = await download_original_thumb(acc, msg, msg_type)
            
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
                        clean_global = int(str(GLOBAL_CHANNEL_ID).split("#")[0].strip())
                        if clean_global != target_chat:
                            await upload_service.upload_file(
                                client,
                                download_result.file_path,
                                msg,
                                msg_type,
                                clean_global,
                                None,
                                caption,
                                None,
                                None,
                                None
                            )
                    except Exception as e:
                        logger.error(f"Global channel upload error: {e}")
            
                # 1-second delay between files
                await asyncio.sleep(1)
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
    """Get message type with accurate text and media detection covering all formats"""
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
    elif getattr(msg, "poll", None):
        return "Poll"
    elif getattr(msg, "location", None):
        return "Location"
    elif getattr(msg, "venue", None):
        return "Venue"
    elif getattr(msg, "contact", None):
        return "Contact"
    elif getattr(msg, "dice", None):
        return "Dice"
    
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
        elif "POLL" in media_str:
            return "Poll"
        elif "LOCATION" in media_str:
            return "Location"
        elif "VENUE" in media_str:
            return "Venue"
        elif "CONTACT" in media_str:
            return "Contact"
        elif "DICE" in media_str:
            return "Dice"
    
    # If text message or caption or web page
    if getattr(msg, "text", None) or getattr(msg, "caption", None) or getattr(msg, "web_page", None):
        return "Text"
    
    return "Unknown"


async def check_file_filters(user_id: int, msg_type: str, msg: Message) -> bool:
    """Check if file type is allowed by user filters"""
    try:
        filters_dict = await db.get_file_preferences(user_id)
        if not filters_dict:
            return True
        
        if msg_type == "Document":
            doc = getattr(msg, "document", None)
            file_name = getattr(doc, "file_name", "") if doc else ""
            mime_type = getattr(doc, "mime_type", "") if doc else ""
            if is_archive_filename(file_name, mime_type):
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


async def get_target_chat(user_id: int, default_chat_id: int) -> Union[int, str]:
    """Get target chat for uploads (returns int chat_id or username string)"""
    chat_id = await db.get_chat_id(user_id)
    if chat_id:
        try:
            return int(str(chat_id).split("#")[0].strip())
        except (ValueError, TypeError):
            return str(chat_id).strip()
    
    if CHANNEL_ID:
        try:
            clean_id = str(CHANNEL_ID).split("#")[0].strip()
            if clean_id:
                try:
                    return int(clean_id)
                except ValueError:
                    return clean_id
        except Exception:
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
    """Generate sanitized filename for download with universal format support"""
    media = None
    if msg_type not in ["Text", "VideoNote", "Poll", "Location", "Venue", "Contact", "Dice"]:
        media = getattr(msg, msg_type.lower(), None)
    elif msg_type == "VideoNote":
        media = getattr(msg, "video_note", None)
    
    # Try to get original filename
    if media and hasattr(media, 'file_name') and media.file_name:
        return sanitize_filename(media.file_name)
    
    # Generate filename based on MIME type or format
    timestamp = int(time.time())
    mime = getattr(media, "mime_type", "") if media else ""
    ext = get_extension_from_mime(mime, "")
    
    if msg_type == "Text":
        return f"text_message_{task_id}_{msgid}_{timestamp}.txt"
    elif msg_type == "Photo":
        return f"photo_{task_id}_{msgid}_{timestamp}{ext or '.jpg'}"
    elif msg_type == "Video":
        return f"video_{task_id}_{msgid}_{timestamp}{ext or '.mp4'}"
    elif msg_type == "VideoNote":
        return f"videonote_{task_id}_{msgid}_{timestamp}.mp4"
    elif msg_type == "Audio":
        return f"audio_{task_id}_{msgid}_{timestamp}{ext or '.mp3'}"
    elif msg_type == "Voice":
        return f"voice_{task_id}_{msgid}_{timestamp}{ext or '.ogg'}"
    elif msg_type == "Animation":
        return f"animation_{task_id}_{msgid}_{timestamp}{ext or '.mp4'}"
    elif msg_type == "Sticker":
        if media:
            if getattr(media, "is_animated", False):
                return f"sticker_{task_id}_{msgid}_{timestamp}.tgs"
            elif getattr(media, "is_video", False):
                return f"sticker_{task_id}_{msgid}_{timestamp}.webm"
        return f"sticker_{task_id}_{msgid}_{timestamp}{ext or '.webp'}"
    elif msg_type == "Document":
        return f"document_{task_id}_{msgid}_{timestamp}{ext or '.bin'}"
    elif msg_type == "Poll":
        return f"poll_{task_id}_{msgid}_{timestamp}.txt"
    elif msg_type in ["Location", "Venue"]:
        return f"location_{task_id}_{msgid}_{timestamp}.txt"
    elif msg_type == "Contact":
        return f"contact_{task_id}_{msgid}_{timestamp}.vcf"
    
    return f"file_{task_id}_{msgid}_{timestamp}{ext or '.bin'}"