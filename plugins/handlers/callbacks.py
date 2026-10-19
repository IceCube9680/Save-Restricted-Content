"""
Callback Query Handlers for Inline Keyboard Buttons
"""

import asyncio
import time
import random
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from pyrogram import Client, filters, enums
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import FloodWait, MessageNotModified

from config import ADMINS, LOGIN_SYSTEM, WAITING_TIME, ENABLE_GLOBAL_CHANNEL, GLOBAL_CHANNEL_ID
from database.mongodb import db
from plugins.security.auth import auth_manager
from plugins.core.utils import (
    get_logger, humanbytes, time_formatter, rate_limiter,
    get_ist_time, truncate_text
)
from plugins.core.animations import ProgressAnimations
from plugins.core.constants import PROGRESS_STYLES, TaskStatus
from plugins.services.queue_manager import queue_manager
from plugins.services.session_manager import session_manager
from plugins.monitoring.metrics import usage_stats

logger = get_logger(__name__)


# ============== PROGRESS CONTROL CALLBACKS ==============

@Client.on_callback_query(filters.regex(r"^(pause|resume|cancel|start|refresh|details|queue|skip)_(\d+)$"))
async def handle_progress_controls(client: Client, callback_query: CallbackQuery):
    """Handle progress control buttons (pause, resume, cancel, etc.)"""
    action = callback_query.data.split("_")[0]
    user_id = int(callback_query.data.split("_")[1])
    
    # Verify user authorization
    if callback_query.from_user.id != user_id and callback_query.from_user.id not in ADMINS:
        await callback_query.answer("⛔ You can only control your own downloads!", show_alert=True)
        return
    
    # Get queue
    queue = queue_manager.get_queue(user_id)
    
    # Check if queue is valid
    if queue.total_tasks == 0:
        await callback_query.answer("❌ Session expired or completed", show_alert=True)
        return
    
    # Re-attach queue to message if context was lost
    if not queue.progress_message_id:
        queue.progress_message_id = callback_query.message.id
    if not queue.chat_id:
        queue.chat_id = callback_query.message.chat.id
    
    # Handle actions
    if action == "pause":
        await queue_manager.pause_queue(user_id)
        await callback_query.answer("⏸️ Batch Paused", show_alert=True)
        
    elif action == "resume":
        await queue_manager.resume_queue(user_id)
        await callback_query.answer("▶️ Batch Resumed", show_alert=True)
        
    elif action == "cancel":
        await queue_manager.cancel_queue(user_id)
        await db.delete_queue_state(user_id)
        from plugins.handlers.messages import batch_temp
        batch_temp.IS_BATCH[user_id] = True
        
        await callback_query.answer("⏹️ Batch Cancelled", show_alert=True)
        
        # Show cancellation animation
        if queue.progress_message_id:
            try:
                cancellation_text = """
⏹️ **BATCH CANCELLED**

✅ Operation stopped successfully.
📊 Progress has been saved.

Thank you for using the bot! 🙏
"""
                await client.edit_message_text(
                    chat_id=queue.chat_id,
                    message_id=queue.progress_message_id,
                    text=cancellation_text
                )
                await asyncio.sleep(2)
                await client.delete_messages(queue.chat_id, queue.progress_message_id)
            except Exception as e:
                logger.debug(f"Error deleting progress message: {e}")
                
    elif action == "start":
        await callback_query.answer("🚀 Starting batch...")
        
        # Start processing if not already running
        if not queue.current_task and queue.queue:
            from plugins.handlers.messages import process_batch
            asyncio.create_task(process_batch(client, user_id))
            
    elif action == "refresh":
        await callback_query.answer("🔄 Refreshing...")
        
        # Force update display
        from plugins.progress_display import update_progress_display
        await update_progress_display(client, user_id, force=True)
        
    elif action == "details":
        queue = queue_manager.get_queue(user_id)
        task = queue.current_task
        
        # Callback answer text is limited to 200 chars and doesn't support markdown
        details = f"📊 Batch: {queue.completed_tasks}/{queue.total_tasks} ({queue.get_batch_progress():.1f}%)\n"
        details += f"❌ Failed: {queue.failed_tasks}\n"
        details += f"⏳ Batch ETA: {time_formatter(queue.get_batch_eta())}\n"
        
        if task:
            fname = task.file_name or "Unknown"
            fname = truncate_text(fname, 20)
            details += f"\n📁 File: {fname}\n"
            details += f"📦 Size: {humanbytes(task.size)}\n"
            details += f"⚡ Speed: {humanbytes(task.speed)}/s\n"
            details += f"⏱️ ETA: {time_formatter(task.eta)}\n"
            details += f"🔋 Progress: {task.progress:.1f}%"
        else:
            details += "\n📁 No active file processing."
            
        await callback_query.answer(details, show_alert=True)

    elif action == "queue":
        queue = queue_manager.get_queue(user_id)
        if not queue.queue:
            await callback_query.answer("📂 Queue is empty", show_alert=True)
            return
            
        text = f"📋 Queue List ({len(queue.queue)} files)\n\n"
        # Show next 10 tasks
        for i, task in enumerate(queue.queue[:10]):
            filename = truncate_text(task.file_name or f"Message {task.msgid}", 30)
            text += f"{i+1}. {filename}\n"
            
        if len(queue.queue) > 10:
            text += f"\n...and {len(queue.queue) - 10} more"
            
        msg = await client.send_message(
            chat_id=callback_query.message.chat.id,
            text=text
        )
        await callback_query.answer("📋 Queue list sent to chat")

        # Delete message after 30 seconds
        async def delete_later():
            await asyncio.sleep(30)
            try:
                await msg.delete()
            except Exception:
                pass
        asyncio.create_task(delete_later())

    elif action == "skip":
        queue = queue_manager.get_queue(user_id)
        if queue.current_task:
            queue.current_task.status = TaskStatus.SKIPPED
            await callback_query.answer("⏭️ Skipping current task...", show_alert=False)
        else:
            await callback_query.answer("❌ No active task to skip", show_alert=True)
    
    # Update progress display
    from plugins.progress_display import update_progress_display
    await update_progress_display(client, user_id, force=True)


# ============== SETTINGS CALLBACKS ==============

@Client.on_callback_query(filters.regex(r"^settings$"))
async def settings_menu(client: Client, callback_query: CallbackQuery):
    """Show settings menu"""
    user_id = callback_query.from_user.id
    
    if not await auth_manager.require_auth(user_id, callback_query):
        return
    
    # Get current settings
    caption = await db.get_caption(user_id)
    chat_id = await db.get_chat_id(user_id)
    progress_style = await db.get_progress_style(user_id)
    filters_dict = await db.get_file_preferences(user_id)
    
    caption_text = truncate_text(caption, 30) if caption else "None"
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
            InlineKeyboardButton("❌ Reset Settings", callback_data="reset_settings"),
            InlineKeyboardButton("🔙 Back", callback_data="back_main")
        ]
    ]
    
    text = f"""
⚙️ **User Settings**

━━━━━━━━━━━━━━━━━━━━

📝 **Caption:** `{caption_text}`
🖼️ **Thumbnail:** {'✅ Set' if await db.get_thumbnail(user_id) else '❌ Not set'}
🎯 **Target Chat:** {chat_text}
🎨 **Progress Style:** `{progress_style}`
🔍 **File Filters:** {'✅ Enabled' if filters_dict else '❌ All files'}

━━━━━━━━━━━━━━━━━━━━

Select an option to configure:
"""
    
    try:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.MARKDOWN
        )
    except MessageNotModified:
        pass
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^set_caption$"))
async def set_caption_prompt(client: Client, callback_query: CallbackQuery):
    """Prompt user to set caption"""
    user_id = callback_query.from_user.id
    
    buttons = [
        [InlineKeyboardButton("❌ Remove Caption", callback_data="remove_caption")],
        [InlineKeyboardButton("🔙 Back", callback_data="settings")]
    ]
    
    current = await db.get_caption(user_id)
    current_text = f"\n\n**Current Caption:**\n`{current}`" if current else ""
    
    text = f"""
📝 **Set Custom Caption**

Send me the caption you want to use for all uploaded files.
You can use HTML formatting.

{current_text}

**Available placeholders:**
• `{{date}}` - Current date
• `{{time}}` - Current time
• `{{user_id}}` - Your user ID

To remove caption, click the button below.
To cancel, use /cancel.
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    # Set user state
    from plugins.handlers.messages import user_states
    user_states[user_id] = {"action": "set_caption"}
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^remove_caption$"))
async def remove_caption(client: Client, callback_query: CallbackQuery):
    """Remove user caption"""
    user_id = callback_query.from_user.id
    
    await db.save_preferences(user_id, caption=None)
    
    await callback_query.answer("✅ Caption removed!", show_alert=True)
    
    # Return to settings
    await settings_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^set_thumbnail$"))
async def set_thumbnail_prompt(client: Client, callback_query: CallbackQuery):
    """Prompt user to set thumbnail"""
    user_id = callback_query.from_user.id
    
    buttons = [
        [InlineKeyboardButton("❌ Remove Thumbnail", callback_data="remove_thumbnail")],
        [InlineKeyboardButton("🔙 Back", callback_data="settings")]
    ]
    
    current = await db.get_thumbnail(user_id)
    current_text = "\n\n✅ **Current thumbnail is set**" if current else "\n\n❌ **No thumbnail set**"
    
    text = f"""
🖼️ **Set Custom Thumbnail**

Send me an image file to use as thumbnail for all video/document uploads.
Supported formats: JPG, PNG, WEBP (square images work best)

{current_text}

To remove thumbnail, click the button below.
To cancel, use /cancel.
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    # Set user state
    from plugins.handlers.messages import user_states
    user_states[user_id] = {"action": "set_thumbnail"}
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^remove_thumbnail$"))
async def remove_thumbnail(client: Client, callback_query: CallbackQuery):
    """Remove user thumbnail"""
    user_id = callback_query.from_user.id
    
    await db.save_preferences(user_id, thumbnail_file_id=None)
    
    await callback_query.answer("✅ Thumbnail removed!", show_alert=True)
    
    # Return to settings
    await settings_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^set_chat$"))
async def set_chat_prompt(client: Client, callback_query: CallbackQuery):
    """Prompt user to set target chat"""
    user_id = callback_query.from_user.id
    
    buttons = [
        [InlineKeyboardButton("❌ Reset to Default", callback_data="reset_chat")],
        [InlineKeyboardButton("🔙 Back", callback_data="settings")]
    ]
    
    current = await db.get_chat_id(user_id)
    current_text = f"\n\n**Current Target Chat:** `{current}`" if current else "\n\n**Current Target Chat:** Default (current chat)"
    
    text = f"""
🎯 **Set Target Chat**

Forward me a message from the chat where you want files to be uploaded.
This can be a channel, group, or your saved messages.

{current_text}

To reset to default, click the button below.
To cancel, use /cancel.
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    # Set user state
    from plugins.handlers.messages import user_states
    user_states[user_id] = {"action": "set_chat"}
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^reset_chat$"))
async def reset_chat(client: Client, callback_query: CallbackQuery):
    """Reset target chat to default"""
    user_id = callback_query.from_user.id
    
    await db.save_preferences(user_id, target_chat_id=None)
    
    await callback_query.answer("✅ Target chat reset to default!", show_alert=True)
    
    # Return to settings
    await settings_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^set_style$"))
async def set_style_menu(client: Client, callback_query: CallbackQuery):
    """Show progress style selection menu"""
    user_id = callback_query.from_user.id
    
    current = await db.get_progress_style(user_id)
    
    buttons = []
    row = []
    
    for i, style in enumerate(PROGRESS_STYLES):
        style_display = f"{style} {'✅' if style == current else ''}"
        row.append(InlineKeyboardButton(style_display, callback_data=f"style_{style}"))
        
        if len(row) == 2 or i == len(PROGRESS_STYLES) - 1:
            buttons.append(row)
            row = []
    
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="settings")])
    
    # Preview different styles
    preview = ""
    target_styles = ["modern", "arrow", "gradient", "block", "circle", "square"]
    preview_styles = [s for s in target_styles if s in PROGRESS_STYLES]

    for style in preview_styles:
        bar = ProgressAnimations.get_progress_bar(65, length=10, style=style)
        preview += f"{style}: {bar} 65%\n"
    
    text = f"""
🎨 **Progress Bar Style**

Current style: **{current}**

Select your preferred progress bar style:

**Preview:**
{preview}

The style will be used for all your downloads.
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^style_(.+)$"))
async def set_style(client: Client, callback_query: CallbackQuery):
    """Set progress style preference"""
    user_id = callback_query.from_user.id
    style = callback_query.data.split("_", 1)[1]
    
    if style in PROGRESS_STYLES:
        await db.save_progress_style(user_id, style)
        
        # Update active queue style immediately
        try:
            queue = queue_manager.get_queue(user_id)
            if queue:
                queue.progress_style = style
        except Exception:
            pass
            
        await callback_query.answer(f"✅ Style set to {style}!", show_alert=True)
    else:
        await callback_query.answer("❌ Invalid style!", show_alert=True)
    
    # Return to style menu
    await set_style_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^set_filters$"))
async def set_filters_menu(client: Client, callback_query: CallbackQuery):
    """Show file filters menu"""
    user_id = callback_query.from_user.id
    
    filters_dict = await db.get_file_preferences(user_id)
    
    buttons = [
        [
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('document', True) else '❌'} Documents",
                callback_data="filter_document"
            ),
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('video', True) else '❌'} Videos",
                callback_data="filter_video"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('audio', True) else '❌'} Audio",
                callback_data="filter_audio"
            ),
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('photo', True) else '❌'} Photos",
                callback_data="filter_photo"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('animation', True) else '❌'} Animations",
                callback_data="filter_animation"
            ),
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('sticker', True) else '❌'} Stickers",
                callback_data="filter_sticker"
            )
        ],
        [
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('voice', True) else '❌'} Voice",
                callback_data="filter_voice"
            ),
            InlineKeyboardButton(
                f"{'✅' if filters_dict.get('zip', True) else '❌'} Archives",
                callback_data="filter_zip"
            )
        ],
        [
            InlineKeyboardButton("✅ Enable All", callback_data="filters_all_on"),
            InlineKeyboardButton("❌ Disable All", callback_data="filters_all_off")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="settings")]
    ]
    
    text = f"""
🔍 **File Type Filters**

Toggle which file types you want to download.
✅ = Allowed, ❌ = Blocked

**Current Settings:**
• Documents: {'✅' if filters_dict.get('document', True) else '❌'}
• Videos: {'✅' if filters_dict.get('video', True) else '❌'}
• Audio: {'✅' if filters_dict.get('audio', True) else '❌'}
• Photos: {'✅' if filters_dict.get('photo', True) else '❌'}
• Animations: {'✅' if filters_dict.get('animation', True) else '❌'}
• Stickers: {'✅' if filters_dict.get('sticker', True) else '❌'}
• Voice: {'✅' if filters_dict.get('voice', True) else '❌'}
• Archives: {'✅' if filters_dict.get('zip', True) else '❌'}

Click on a button to toggle the filter.
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^filter_(.+)$"))
async def toggle_filter(client: Client, callback_query: CallbackQuery):
    """Toggle individual file filter"""
    user_id = callback_query.from_user.id
    filter_type = callback_query.data.split("_", 1)[1]
    
    # Get current filters
    filters_dict = await db.get_file_preferences(user_id)
    
    # Map archive filter
    if filter_type == "zip":
        db_key = "zip"
    else:
        db_key = filter_type
    
    # Toggle
    current = filters_dict.get(db_key, True)
    filters_dict[db_key] = not current
    
    # Save
    await db.save_file_preferences(user_id, filters_dict)
    
    await callback_query.answer(f"{'✅ Enabled' if filters_dict[db_key] else '❌ Disabled'} {filter_type}!", show_alert=True)
    
    # Refresh menu
    await set_filters_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^filters_all_on$"))
async def filters_all_on(client: Client, callback_query: CallbackQuery):
    """Enable all filters"""
    user_id = callback_query.from_user.id
    
    filters_dict = {
        "document": True,
        "video": True,
        "audio": True,
        "photo": True,
        "animation": True,
        "sticker": True,
        "voice": True,
        "zip": True
    }
    
    await db.save_file_preferences(user_id, filters_dict)
    
    await callback_query.answer("✅ All filters enabled!", show_alert=True)
    
    # Refresh menu
    await set_filters_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^filters_all_off$"))
async def filters_all_off(client: Client, callback_query: CallbackQuery):
    """Disable all filters"""
    user_id = callback_query.from_user.id
    
    filters_dict = {
        "document": False,
        "video": False,
        "audio": False,
        "photo": False,
        "animation": False,
        "sticker": False,
        "voice": False,
        "zip": False
    }
    
    await db.save_file_preferences(user_id, filters_dict)
    
    await callback_query.answer("❌ All filters disabled!", show_alert=True)
    
    # Refresh menu
    await set_filters_menu(client, callback_query)


@Client.on_callback_query(filters.regex(r"^my_stats$"))
async def my_stats(client: Client, callback_query: CallbackQuery):
    """Show user statistics"""
    user_id = callback_query.from_user.id
    
    # Get user data
    user = await db.get_user(user_id)
    
    if not user:
        user = {}

    # Get detailed stats from history
    stats = await db.get_user_download_stats(user_id)
    
    # Fallback to user profile stats if history is empty
    if stats["total"] == 0 and user.get("total_downloads", 0) > 0:
        stats["total"] = user.get("total_downloads", 0)
        stats["successful"] = user.get("total_downloads", 0)
        stats["total_size"] = user.get("total_bandwidth", 0)
    
    # Get queue info
    queue = queue_manager.get_queue(user_id)
    queue_info = await queue_manager.get_queue_info(user_id)
    
    # Format dates
    created_at = user.get('created_at', datetime.now())
    created_str = created_at.strftime('%Y-%m-%d %H:%M')
    
    last_active = user.get('last_active', datetime.now())
    last_active_str = last_active.strftime('%Y-%m-%d %H:%M')
    
    text = f"""
📊 **Your Statistics**

━━━━━━━━━━━━━━━━━━━━

👤 **User ID:** `{user_id}`
📅 **Joined:** {created_str}
🕐 **Last Active:** {last_active_str}

━━━━━━━━━━━━━━━━━━━━

📥 **Downloads:**
• Total: `{stats.get('total', 0)}`
• Successful: `{stats.get('successful', 0)}`
• Failed: `{stats.get('failed', 0)}`
• Total Size: `{humanbytes(stats.get('total_size', 0))}`

━━━━━━━━━━━━━━━━━━━━

🔄 **Current Queue:**
• Active: `{'Yes' if queue.current_task else 'No'}`
• Queued: `{len(queue.queue)}`
• Completed: `{queue_info['completed']}`
• Failed: `{queue_info['failed']}`

━━━━━━━━━━━━━━━━━━━━

[🔙 Back to Settings](callback:settings)
"""
    
    buttons = [[InlineKeyboardButton("🔙 Back", callback_data="settings")]]
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^reset_settings$"))
async def reset_settings_prompt(client: Client, callback_query: CallbackQuery):
    """Confirm reset settings"""
    user_id = callback_query.from_user.id
    
    buttons = [
        [
            InlineKeyboardButton("✅ Yes, Reset", callback_data="confirm_reset"),
            InlineKeyboardButton("❌ No", callback_data="settings")
        ]
    ]
    
    text = """
⚠️ **Reset Settings**

Are you sure you want to reset all your settings to default?

This will:
• Remove custom caption
• Remove custom thumbnail
• Reset target chat to default
• Reset progress style to modern
• Enable all file filters

This action cannot be undone!
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^confirm_reset$"))
async def confirm_reset(client: Client, callback_query: CallbackQuery):
    """Confirm and execute settings reset"""
    user_id = callback_query.from_user.id
    
    # Reset to default settings
    await db.save_preferences(
        user_id,
        caption=None,
        thumbnail_file_id=None,
        target_chat_id=None,
        progress_style="modern",
        file_filters={
            "document": True,
            "video": True,
            "audio": True,
            "photo": True,
            "animation": True,
            "sticker": True,
            "voice": True,
            "zip": True
        }
    )
    
    await callback_query.answer("✅ Settings reset to default!", show_alert=True)
    
    # Return to settings
    await settings_menu(client, callback_query)


# ============== MAIN MENU CALLBACKS ==============

@Client.on_callback_query(filters.regex(r"^back_main$"))
async def back_to_main(client: Client, callback_query: CallbackQuery):
    """Return to main menu"""
    user_id = callback_query.from_user.id
    
    welcome_emoji = ["👋", "🤖", "🚀", "✨", "🎉"][int(time.time()) % 5]
    
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
    
    text = f"""
{welcome_emoji} **Welcome {callback_query.from_user.first_name}!** {welcome_emoji} 

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
    
    try:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.MARKDOWN
        )
    except MessageNotModified:
        pass
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^terms$"))
async def terms_of_service(client: Client, callback_query: CallbackQuery):
    """Show terms of service"""
    buttons = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
    
    text = """
📜 **Terms of Service**

By using this bot, you agree to the following terms:

1. **Usage:** This bot is for personal use only. Do not use for spam or illegal activities.

2. **Content:** You are responsible for the content you download. Respect copyright laws.

3. **Privacy:** Your session data is encrypted.

4. **Rate Limits:** Excessive usage may result in temporary restrictions.

5. **Changes:** Terms may be updated without prior notice.

6. **Liability:** The bot is provided "as is" without warranties.

Last updated: February 2026
"""
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^help$"))
async def help_callback(client: Client, callback_query: CallbackQuery):
    """Show help message"""
    buttons = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
    
    text = """
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
    
    await callback_query.message.edit_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.MARKDOWN
    )
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex(r"^status$"))
async def bot_status(client: Client, callback_query: CallbackQuery):
    """Show bot status"""
    from plugins.monitoring.metrics import usage_stats
    from plugins.progress_display import progress_display_manager
    
    stats = usage_stats.get_summary()
    db_stats = await usage_stats.get_database_stats()
    stats.update(db_stats)
    
    offset = progress_display_manager.config.TIMEZONE_OFFSET
    now = datetime.now() + timedelta(hours=offset)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%I:%M:%S %p")
    
    buttons = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="status")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ]
    
    text = f"""
📊 **Bot Status**

━━━━━━━━━━━━━━━━━━━━

⏱️ **Uptime:** `{stats['uptime']}`
👥 **Total Users:** `{stats.get('total_users', 'N/A')}`
🟢 **Active Now:** `{stats.get('active_sessions', 0)}`

━━━━━━━━━━━━━━━━━━━━

📥 **Downloads Today:** `{stats['total_downloads']}`
📤 **Uploads Today:** `{stats['total_uploads']}`
✅ **Success Rate:** `{stats['success_rate']}`
📦 **Bandwidth:** `{stats['total_bandwidth']}`

━━━━━━━━━━━━━━━━━━━━

📅 **Date:** {date_str}
⏰ **Time:** {time_str}

━━━━━━━━━━━━━━━━━━━━

[🤖 @{client.me.username}](https://t.me/{client.me.username})
"""
    
    try:
        await callback_query.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )
    except MessageNotModified:
        pass
    
    await callback_query.answer()


# ============== ADMIN CALLBACKS ==============

@Client.on_callback_query(filters.regex(r"^admin_(.+)$") & filters.user(ADMINS))
async def admin_callbacks(client: Client, callback_query: CallbackQuery):
    """Handle admin callback queries"""
    action = callback_query.data.split("_", 1)[1]
    
    if action == "users":
        # Show user management menu
        buttons = [
            [InlineKeyboardButton("📋 List Users", callback_data="admin_list_users")],
            [InlineKeyboardButton("🔍 Search User", callback_data="admin_search_user")],
            [InlineKeyboardButton("📊 Statistics", callback_data="admin_user_stats")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
        ]
        
        text = "👥 **User Management**\n\nSelect an option:"
        
        await callback_query.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    elif action == "system":
        # Show system management menu
        buttons = [
            [InlineKeyboardButton("💾 Backup", callback_data="admin_backup")],
            [InlineKeyboardButton("🔄 Restart", callback_data="admin_restart")],
            [InlineKeyboardButton("🧹 Cleanup", callback_data="admin_cleanup")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")]
        ]
        
        text = "⚙️ **System Management**\n\nSelect an option:"
        
        await callback_query.message.edit_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    
    await callback_query.answer()


# ============== ERROR HANDLING ==============

@Client.on_callback_query()
async def unknown_callback(client: Client, callback_query: CallbackQuery):
    """Handle unknown callback queries"""
    await callback_query.answer("❌ Unknown button or expired session", show_alert=True)