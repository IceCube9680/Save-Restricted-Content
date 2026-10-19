"""
Handlers Package - Register all command and message handlers
"""

from pyrogram import Client

from . import commands
from . import callbacks
from . import messages


def register_all_handlers(client: Client):
    """Register all handlers with the client"""
    # This function is called from bot.py
    # Handlers are automatically registered via decorators
    pass