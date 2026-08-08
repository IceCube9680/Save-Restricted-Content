"""
Command Handlers for the Bot
"""

import os
import sys
import asyncio
import shutil
import time
from datetime import datetime, timedelta
from typing import Optional
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired,
    SessionPasswordNeeded, FloodWait, PasswordHashInvalid
)
from pyrogram import Client, filters, enums, ContinuePropagation
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import (
    ADMINS, LOG_CHANNEL, LOGIN_SYSTEM, ERROR_MESSAGE,
    WAITING_TIME, ENABLE_GLOBAL_CHANNEL, GLOBAL_CHANNEL_ID,
    BACKUP_ENABLED, BACKUP_DIR, MAX_BACKUP_COUNT
)
from database.mongodb import db
from plugins.security.auth import auth_manager, is_authorized
from plugins.security.encryption import encrypt_data
from plugins.core.utils import (
    get_logger, humanbytes, time_formatter, get_ist_time,
    rate_limiter, ensure_directory
)
from plugins.core.animations import ProgressAnimations
from plugins.services.queue_manager import queue_manager
from plugins.monitoring.metrics import usage_stats
from plugins.monitoring.health import HealthMonitor

logger = get_logger(__name__)

START_TIME = time.time()

# Store user states (use a proper database in production)
user_sessions = {}

# ============== START COMMAND ==============

@Client.on_message(filters.command(["start"]))
async def start_command(client: Client, message: Message):
    """Handle /start command"""
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
    
    # Welcome message with animation
    welcome_emojis = ["👋", "🤖", "🚀", "✨", "🎉"]
    welcome_emoji = welcome_emojis[int(time.time()) % len(welcome_emojis)]
    
    # Create buttons
    buttons = [
        [
            InlineKeyboardButton("❣️ Developer", url="https://icecube9680.github.io"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings")
        ],
        [
            InlineKeyboardButton('🔍 Support Group', url='https://t.me/movieverse_discussion_2'),
            InlineKeyboardButton('🤖 Update Channel', url='https://t.me/ice_verse')
        ],
        [
            InlineKeyboardButton("📜 Terms", callback_data="terms"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(buttons)
    
    welcome_text = f"""
{welcome_emoji} **Welcome {message.from_user.first_name}!** {welcome_emoji} 

I'm **Save Restricted Content Bot** – your ultimate tool to download **private and restricted content** from Telegram with ease!

🚀 **What I Can Do:**
• 🔓 Download from **private channels/groups** (after login)
• 📦 **Batch download** multiple messages at once.
• 🎯 Set custom **captions, thumbnails, and target chat**
• ⏸️ **Pause/Resume/Cancel** downloads anytime
• 📊 Track your **download stats** and progress

🔐 **Quick Start:**
1. Use `/login` to connect your Telegram account (required for private content)
2. Send any **public post link** or **private message link**
3. For batch: `https://t.me/channel/100-200`

⚡ **Need Help?** Use `/help` or click the buttons below.

Let's start saving content! 🎉
"""
    
    await client.send_message(
        chat_id=message.chat.id,
        text=welcome_text,
        reply_markup=reply_markup,
        reply_to_message_id=message.id
    )


# ============== HELP COMMAND ==============

@Client.on_message(filters.command(["help"]))
async def help_command(client: Client, message: Message):
    """Handle /help command"""
    user_id = message.from_user.id
    
    if not await auth_manager.require_auth(user_id, message):
        return
    
    help_text = """
🎬 **HOW TO USE:**

1. **For Public Content:**
   Just send the post link:
   `https://t.me/channel/123`

2. **For Private Content:**
   - First use `/login`
   - Then send private links:
   `https://t.me/c/chat_id/123`

3. **Batch Downloads:**
   `https://t.me/channel/100-200`
   Downloads posts 100 to 200

4. **Bot Messages:**
   `https://t.me/b/botname/message_id`

🔄 **CONTROLS:**
• ⏸️ Pause - Pause current batch
• ▶️ Resume - Resume paused batch
• ⏹️ Stop - Cancel current operation
• 🔄 Refresh - Update progress display

⚙️ **SETTINGS:**
Use `/settings` to configure:
• File type filters
• Custom captions
• Thumbnails
• Target channel

**Happy downloading!** 🎉
"""
    
    await client.send_message(
        chat_id=message.chat.id,
        text=help_text
    )


# ============== CANCEL COMMAND ==============

@Client.on_message(filters.command(["cancel"]))
async def cancel_command(client: Client, message: Message):
    """Handle /cancel command"""
    user_id = message.from_user.id
    
    if not await auth_manager.require_auth(user_id, message):
        return
    
    # Cancel queue
    await queue_manager.cancel_queue(user_id)
    
    # Clear batch flag
    from plugins.handlers.messages import batch_temp
    batch_temp.IS_BATCH[user_id] = True
    
    # Clear from database
    await db.delete_queue_state(user_id)
    
    cancellation_emoji = ["⏹️", "🚫", "✋", "🛑"][int(time.time()) % 4]
    
    await client.send_message(
        chat_id=message.chat.id,
        text=f"{cancellation_emoji} **Batch Successfully Cancelled.**\n\nAll operations have been stopped and resources freed."
    )


# ============== STATS COMMAND (ADMIN ONLY) ==============

@Client.on_message(filters.command(["stats", "status"]) & filters.user(ADMINS))
async def stats_command(client: Client, message: Message):
    """Handle /stats command (admin only)"""
    loading_msg = await message.reply_text("🔄 **Fetching System Stats...**")
    
    # Basic stats
    uptime = time_formatter(time.time() - START_TIME)
    
    # Disk usage
    try:
        total, used, free = shutil.disk_usage(".")
        total_disk = humanbytes(total)
        used_disk = humanbytes(used)
        free_disk = humanbytes(free)
    except:
        total_disk = used_disk = free_disk = "N/A"
    
    # Get system stats
    cpu_percent = "N/A"
    ram_percent = "N/A"
    cpu_bar = ""
    ram_bar = ""
    
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        ram_percent = psutil.virtual_memory().percent
        
        cpu_bar = ProgressAnimations.get_progress_bar(cpu_percent, length=10, style="modern")
        ram_bar = ProgressAnimations.get_progress_bar(ram_percent, length=10, style="modern")
    except ImportError:
        # Try Linux /proc stats
        try:
            with open("/proc/stat") as f:
                fields1 = [float(column) for column in f.readline().strip().split()[1:]]
            await asyncio.sleep(1)
            with open("/proc/stat") as f:
                fields2 = [float(column) for column in f.readline().strip().split()[1:]]
            
            last_idle, last_total = fields1[3], sum(fields1)
            idle, total = fields2[3], sum(fields2)
            idle_delta, total_delta = idle - last_idle, total - last_total
            cpu_percent = round(100.0 * (1.0 - idle_delta / total_delta), 1)
            
            with open("/proc/meminfo") as f:
                meminfo = {line.split(':')[0]: int(line.split()[1]) for line in f}
            
            total_ram = meminfo.get('MemTotal', 0)
            available_ram = meminfo.get('MemAvailable', 0)
            ram_percent = round(100 * (1 - (available_ram / total_ram)), 1)
            
            cpu_bar = ProgressAnimations.get_progress_bar(cpu_percent, length=10, style="modern")
            ram_bar = ProgressAnimations.get_progress_bar(ram_percent, length=10, style="modern")
        except:
            pass
    
    # Get usage statistics
    stats = usage_stats.get_summary()
    
    # Get database stats
    db_stats = await usage_stats.get_database_stats()
    
    # Format stats
    status_emoji = ["🤖", "⚡", "🔋", "🖥️"][int(time.time()) % 4]
    
    text = f"""
{status_emoji} **SYSTEM STATUS**

⏱️ **Uptime:** {uptime}

💻 **System Load:**
├ CPU: {cpu_bar} **{cpu_percent}%**
└ RAM: {ram_bar} **{ram_percent}%**

💾 **Disk Usage:**
├ Total: {total_disk}
├ Used:  {used_disk}
└ Free:  {free_disk}

📊 **Bot Statistics:**
├ 👥 Total Users: {db_stats.get('total_users', 0)}
├ 🟢 Active Users: {db_stats.get('active_users', 0)}
├ 🔐 Active Sessions: {db_stats.get('active_sessions', 0)}
├ 📥 Downloads: {db_stats.get('global_downloads', stats['total_downloads'])}
├ 📤 Uploads: {db_stats.get('global_uploads', stats['total_uploads'])}
├ ✅ Success Rate: {stats['success_rate']}
└ 📦 Bandwidth: {humanbytes(db_stats.get('global_bandwidth', 0)) if 'global_bandwidth' in db_stats else stats['total_bandwidth']}

⚡ **Performance:**
├ 📥 Download Rate: {stats['download_rate']}
├ 📤 Upload Rate: {stats['upload_rate']}
└ 🔄 Active Queues: {len(queue_manager.user_queues)}
"""
    
    await loading_msg.edit_text(text)


# ============== USERS COMMAND (ADMIN ONLY) ==============

@Client.on_message(filters.command(["users"]) & filters.user(ADMINS))
async def users_command(client: Client, message: Message):
    """Handle /users command (admin only)"""
    status_msg = await message.reply_text("🔄 Fetching user data...")
    
    try:
        # Get total users
        total_users = await db.total_users_count()
        
        # Get all users
        users = await db.get_all_users()
        
        # Get active sessions
        active_sessions = await db.get_active_sessions()
        active_session_ids = {s['user_id'] for s in active_sessions}
        
        # Build output
        output_lines = [
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            f"👥 **Total Users:** {total_users}",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            "**📋 User List:**",
            ""
        ]
        
        for user in users[:20]:  # Limit to 20 users to avoid message too long
            user_id = user.get('id') or user.get('_id') or user.get('user_id')
            if not user_id:
                continue
            name = user.get('first_name', 'Unknown')
            username = user.get('username')
            username_display = f"@{username}" if username else "None"
            created = user.get('created_at')
            created_str = created.strftime('%Y-%m-%d') if created else 'Unknown'
            
            is_active = user_id in active_session_ids
            is_banned = user.get('is_banned', False)
            
            status_icon = "🟢" if is_active else "⚪"
            ban_icon = "🔴" if is_banned else "✅"
            
            output_lines.append(f"{status_icon} **ID:** `{user_id}`")
            output_lines.append(f"   **Name:** {name}")
            output_lines.append(f"   **Username:** {username_display}")
            output_lines.append(f"   **Joined:** {created_str}")
            output_lines.append(f"   **Status:** {ban_icon} {'Banned' if is_banned else 'Active'}")
            output_lines.append("")
        
        if len(users) > 20:
            output_lines.append(f"... and {len(users) - 20} more users")
        
        text = "\n".join(output_lines)
        
        # Split message if too long
        if len(text) > 4096:
            with open("users_list.txt", "w", encoding="utf-8") as f:
                f.write(text)
            await message.reply_document("users_list.txt", caption="👥 Users List")
            await status_msg.delete()
            os.remove("users_list.txt")
        else:
            await status_msg.edit_text(text)
            
    except Exception as e:
        logger.error(f"Error in users command: {e}")
        await status_msg.edit_text(f"❌ **Error:** {str(e)[:200]}")


# ============== BACKUP COMMAND (ADMIN ONLY) ==============

@Client.on_message(filters.command(["backup"]) & filters.user(ADMINS))
async def backup_command(client: Client, message: Message):
    """Handle /backup command (admin only)"""
    if not BACKUP_ENABLED:
        await message.reply_text("❌ **Backup is disabled in configuration.**")
        return
    
    status_msg = await message.reply_text("💾 **Creating database backup...**")
    
    try:
        # Create backup directory
        backup_dir = ensure_directory(BACKUP_DIR)
        
        # Generate backup filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"backup_{timestamp}.db")
        
        # MongoDB Backup (JSON Dump of Users)
        import aiofiles
        import json
        
        backup_file = os.path.join(backup_dir, f"backup_users_{timestamp}.json")
        
        # Get all users
        users = await db.get_all_users()
        
        # Convert datetime objects to string
        def default_converter(o):
            if isinstance(o, datetime):
                return o.isoformat()
            return str(o)
            
        async with aiofiles.open(backup_file, 'w') as f:
            await f.write(json.dumps(users, default=default_converter, indent=2))
        
        # Get file size
        size = os.path.getsize(backup_file)
        
        # Log backup
        await db.log_backup(backup_file, size, "success")
        
        # Cleanup old backups
        await cleanup_old_backups(backup_dir)
        
        await status_msg.edit_text(
            f"✅ **Backup Created Successfully!**\n\n"
            f"📁 **File:** `{os.path.basename(backup_file)}`\n"
            f"👥 **Users Dumped:** {len(users)}\n"
            f"📦 **Size:** {humanbytes(size)}\n"
            f"⏰ **Time:** {timestamp}"
        )
            
    except Exception as e:
        logger.error(f"Backup error: {e}")
        await db.log_backup("", 0, "failed", str(e))
        await status_msg.edit_text(f"❌ **Backup Failed**\n\nError: `{str(e)[:200]}`")


async def cleanup_old_backups(backup_dir: str):
    """Keep only the latest MAX_BACKUP_COUNT backups"""
    try:
        # Get all backup files
        files = []
        for f in os.listdir(backup_dir):
            if f.startswith("backup_") and f.endswith(".db"):
                file_path = os.path.join(backup_dir, f)
                files.append((os.path.getmtime(file_path), file_path))
        
        # Sort by date (oldest first)
        files.sort()
        
        # Remove oldest files
        while len(files) > MAX_BACKUP_COUNT:
            _, file_path = files.pop(0)
            os.remove(file_path)
            logger.info(f"Removed old backup: {file_path}")
            
    except Exception as e:
        logger.error(f"Error cleaning old backups: {e}")


# ============== BROADCAST COMMAND (ADMIN ONLY) ==============

@Client.on_message(filters.command(["broadcast"]) & filters.user(ADMINS))
async def broadcast_command(client: Client, message: Message):
    """Handle /broadcast command (admin only)"""
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Usage:** `/broadcast <message>`\n\n"
            "Send a message to all users of the bot."
        )
        return
    
    broadcast_text = message.text.split(" ", 1)[1]
    
    status_msg = await message.reply_text("📢 **Broadcasting message to all users...**")
    
    try:
        users = await db.get_all_users()
        success = 0
        failed = 0
        
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("🤖 Bot", url=f"https://t.me/{client.me.username}")
        ]])
        
        for user in users[:100]:  # Limit to 100 users to avoid flood wait
            user_id = user.get('id') or user.get('_id') or user.get('user_id')
            if not user_id:
                continue
            try:
                await client.send_message(
                    user_id,
                    f"📢 **Broadcast Message**\n\n{broadcast_text}",
                    reply_markup=buttons
                )
                success += 1
                await asyncio.sleep(0.5)  # Avoid flood wait
            except Exception:
                failed += 1
        
        await status_msg.edit_text(
            f"✅ **Broadcast Completed**\n\n"
            f"📨 **Sent:** {success}\n"
            f"❌ **Failed:** {failed}"
        )
        
    except Exception as e:
        await status_msg.edit_text(f"❌ **Broadcast Failed**\n\nError: `{str(e)[:200]}`")


# ============== RESTART COMMAND (ADMIN ONLY) ==============

@Client.on_message(filters.command(["restart"]) & filters.user(ADMINS))
async def restart_command(client: Client, message: Message):
    """Handle /restart command (admin only)"""
    restart_msg = await message.reply_text("🔄 **Restarting bot...**")
    
    try:
        # Notify about restart
        await restart_msg.edit_text(
            "🔄 **Bot is restarting...**\n\n"
            "Please wait 5-10 seconds."
        )
        
        # Perform cleanup manually to avoid deadlock
        from plugins.services.session_manager import session_manager
        from database.mongodb import close_db
        
        await session_manager.close_all_sessions()
        await close_db()
        
        python = sys.executable
        os.execl(python, python, *sys.argv)
        
    except Exception as e:
        await restart_msg.edit_text(f"❌ **Restart Failed**\n\nError: `{str(e)[:200]}`")


# ============== SETTINGS COMMAND ==============

@Client.on_message(filters.command(["settings"]))
async def settings_command(client: Client, message: Message):
    """Handle /settings command"""
    user_id = message.from_user.id
    
    if not await auth_manager.require_auth(user_id, message):
        return
    
    # Get current settings
    caption = await db.get_caption(user_id)
    chat_id = await db.get_chat_id(user_id)
    progress_style = await db.get_progress_style(user_id)
    filters = await db.get_file_preferences(user_id)
    
    caption_text = caption[:30] + "..." if caption and len(caption) > 30 else caption or "None"
    chat_text = f"`{chat_id}`" if chat_id else "Default"
    
    buttons = [
        [
            InlineKeyboardButton("📝 Caption", callback_data="set_caption"),
            InlineKeyboardButton("🖼️ Thumbnail", callback_data="set_thumbnail")
        ],
        [
            InlineKeyboardButton("🎯 Target Chat", callback_data="set_chat"),
            InlineKeyboardButton("🎨 Progress Style", callback_data="set_style")
        ],
        [
            InlineKeyboardButton("🔍 File Filters", callback_data="set_filters"),
            InlineKeyboardButton("📊 My Stats", callback_data="my_stats")
        ],
        [
            InlineKeyboardButton("❌ Reset Settings", callback_data="reset_settings")
        ]
    ]
    
    text = f"""
⚙️ **User Settings**

━━━━━━━━━━━━━━━━━━━━

📝 **Caption:** `{caption_text}`
🖼️ **Thumbnail:** {'✅ Set' if await db.get_thumbnail(user_id) else '❌ Not set'}
🎯 **Target Chat:** {chat_text}
🎨 **Progress Style:** `{progress_style}`
🔍 **File Filters:** {'✅ Enabled' if filters else '❌ All files'}

━━━━━━━━━━━━━━━━━━━━

Select an option to configure:
"""
    
    await client.send_message(
        chat_id=message.chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        reply_to_message_id=message.id
    )


# ============== LOGIN COMMAND ==============

@Client.on_message(filters.command("login"))
async def login_command(client, message):
    user_id = message.from_user.id

    # If already logged in, inform
    existing_session = await db.get_session(user_id)
    if existing_session:
        await message.reply_text(
            "✅ **You are already logged in.**\n\n"
            "Use /logout if you want to switch accounts."
        )
        return

    # Clear any previous state
    if user_id in user_sessions:
        del user_sessions[user_id]

    # Send the first instruction
    text = """
**How To Create Api Id And Api Hash**

1. Go To my.telegram.org
2. Login With Your Telegram Account
3. Click On API Development Tools
4. Fill The Form And You Will Get Your API ID And API HASH

**Send Your API ID.**

Click On /skip To Skip This Process

NOTE :- If You Skip This Then Your Account Ban Chance Is High.
"""
    await message.reply_text(text)

    # Set initial state
    user_sessions[user_id] = {"step": "api_id"}

async def handle_login_steps(client: Client, message: Message):
    """Handle all login steps with clear instructions and cancellation."""
    user_id = message.from_user.id
    text = message.text.strip()

    # If user is not in login process, ignore
    if user_id not in user_sessions:
        return

    state = user_sessions[user_id]
    step = state.get("step")

    # Handle /cancel at any step
    if text.lower() == "/cancel":
        # Clean up temp client if exists
        if "client" in state:
            try:
                await state["client"].disconnect()
            except:
                pass
        del user_sessions[user_id]
        await message.reply_text("❌ **Login cancelled.**")
        return

    # If user sends any other command (like /start, /help) – cancel login
    if text.startswith("/") and text.lower() != "/cancel":
        # Cancel login because user issued another command
        if "client" in state:
            try:
                await state["client"].disconnect()
            except:
                pass
        del user_sessions[user_id]
        await message.reply_text("❌ **Login cancelled because you started another command.**")
        return

    # --- Step 1: API ID ---
    if step == "api_id":
        if text.lower() == "/skip":
            from config import API_ID, API_HASH
            state["api_id"] = API_ID
            state["api_hash"] = API_HASH
            state["step"] = "phone"
            await message.reply_text(
                "✅ **API ID & API HASH skipped!** Using default system credentials.\n\n"
                "Please send your phone number which includes country code.\n"
                "Example: `+13124562345`, `+9171828181889`"
            )
            return

        if not text.isdigit():
            await message.reply_text("❌ **Invalid API ID.** Must be a number.\n\nLogin cancelled.")
            del user_sessions[user_id]
            return

        state["api_id"] = int(text)
        state["step"] = "api_hash"
        await message.reply_text(
            "✅ **API ID saved!**\n\n"
            "Now send me your **API HASH** (from my.telegram.org)."
        )
        return

    # --- Step 2: API HASH ---
    if step == "api_hash":
        # API hash can be alphanumeric, no strict validation
        state["api_hash"] = text
        state["step"] = "phone"
        await message.reply_text(
            "✅ **API HASH saved!**\n\n"
            "Please send your phone number which includes country code.\n"
            "Example: `+13124562345`, `+9171828181889`"
        )
        return

    # --- Step 3: Phone Number ---
    if step == "phone":
        phone = text.strip()
        # Very basic validation – must start with '+'
        if not phone.startswith("+"):
            await message.reply_text(
                "❌ **Invalid phone number.** Must include country code (e.g., +1234567890).\n\nLogin cancelled."
            )
            del user_sessions[user_id]
            return

        try:
            # Create temporary client
            temp_client = Client(
                f"temp_{user_id}",
                api_id=state["api_id"],
                api_hash=state["api_hash"],
                in_memory=True
            )
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone)

            state["client"] = temp_client
            state["phone"] = phone
            state["phone_code_hash"] = sent_code.phone_code_hash
            state["step"] = "code"

            await message.reply_text(
                "📱 **Sending OTP...**\n\n"
                "Please check for an OTP in official telegram account. If you got it, send OTP here after reading the below format.\n\n"
                "If OTP is 12345, please send it as `1 2 3 4 5`.\n\n"
                "Enter /cancel to cancel The Process"
            )

        except PhoneNumberInvalid:
            await message.reply_text("❌ **Invalid phone number.**\n\nLogin cancelled.")
            del user_sessions[user_id]
        except FloodWait as e:
            await message.reply_text(f"⏳ Too many attempts. Please wait {e.value} seconds.\n\nLogin cancelled.")
            del user_sessions[user_id]
        except Exception as e:
            logger.error(f"Login phone error: {e}")
            await message.reply_text(f"❌ Error: {str(e)[:200]}\n\nLogin cancelled.")
            del user_sessions[user_id]
        return

    # --- Step 4: OTP Code ---
    if step == "code":
        # Remove spaces from the code
        code = text.replace(" ", "")
        temp_client = state["client"]

        try:
            # Try to sign in
            await temp_client.sign_in(
                phone_number=state["phone"],
                phone_code_hash=state["phone_code_hash"],
                phone_code=code
            )

            # If we reach here, login succeeded without 2FA
            session_string = await temp_client.export_session_string()
            encrypted_session = encrypt_data(session_string)

            # Save session permanently
            await db.save_session(
                user_id,
                encrypted_session,
                state["api_id"],
                state["api_hash"]
            )

            # Cleanup
            await temp_client.disconnect()
            del user_sessions[user_id]

            await message.reply_text(
                "✅ **Account Login Successfully.**\n\n"
                "If You Get Any Error Related To AUTH KEY Then `/logout` first and `/login` again."
            )
            usage_stats.increment("active_sessions")
            return

        except SessionPasswordNeeded:
            # 2FA is enabled – ask for password
            state["step"] = "password"
            await message.reply_text(
                "🔐 **Your account has enabled two-step verification.**\n\n"
                "Please provide the password.\n\n"
                "Enter /cancel to cancel The Process"
            )
            return

        except PhoneCodeInvalid:
            await message.reply_text("❌ **Invalid OTP.**\n\nLogin cancelled.")
            # Cleanup and delete state
            try:
                await temp_client.disconnect()
            except:
                pass
            del user_sessions[user_id]
            return

        except PhoneCodeExpired:
            await message.reply_text("❌ **OTP expired.** Please start over with /login.\n\nLogin cancelled.")
            try:
                await temp_client.disconnect()
            except:
                pass
            del user_sessions[user_id]
            return

        except FloodWait as e:
            await message.reply_text(f"⏳ Too many attempts. Please wait {e.value} seconds.\n\nLogin cancelled.")
            del user_sessions[user_id]
            return

        except Exception as e:
            logger.error(f"Login code error: {e}")
            await message.reply_text(f"❌ Error: {str(e)[:200]}\n\nLogin cancelled.")
            del user_sessions[user_id]
            return

    # --- Step 5: 2FA Password ---
    if step == "password":
        temp_client = state["client"]
        try:
            # Check the password
            await temp_client.check_password(text)
            # If correct, finalize login
            session_string = await temp_client.export_session_string()
            encrypted_session = encrypt_data(session_string)

            await db.save_session(
                user_id,
                encrypted_session,
                state["api_id"],
                state["api_hash"]
            )

            await temp_client.disconnect()
            del user_sessions[user_id]

            await message.reply_text(
                "✅ **Account Login Successfully (2FA).**\n\n"
                "If You Get Any Error Related To AUTH KEY Then `/logout` first and `/login` again."
            )
            usage_stats.increment("active_sessions")
            return

        except PasswordHashInvalid:
            await message.reply_text(
                "❌ **Invalid password.**\n\n"
                "Please try again or /cancel to cancel."
            )
            return

        except Exception as e:
            logger.error(f"Login 2FA error: {e}")
            await message.reply_text(f"❌ Error: {str(e)[:200]}\n\nLogin cancelled.")
            del user_sessions[user_id]
            return


# ============== LOGOUT COMMAND ==============

@Client.on_message(filters.command(["logout"]))
async def logout_command(client: Client, message: Message):
    """Handle /logout command"""
    user_id = message.from_user.id
    
    if not await auth_manager.require_auth(user_id, message):
        return
    
    if not LOGIN_SYSTEM:
        await message.reply_text(
            "❌ **Login System is Disabled**\n\n"
            "This bot is configured to use a global string session."
        )
        return
    
    # Check if logged in
    existing_session = await db.get_session(user_id)
    if not existing_session:
        await message.reply_text(
            "❌ **You are not logged in!**\n\n"
            "Use /login to login first."
        )
        return
    
    # Delete session
    await db.delete_session(user_id)
    
    # Also clear from session manager
    from plugins.services.session_manager import session_manager
    await session_manager.remove_session(user_id)
    
    await message.reply_text(
        "✅ **Successfully Logged Out!**\n\n"
        "Your session has been removed from the bot."
    )