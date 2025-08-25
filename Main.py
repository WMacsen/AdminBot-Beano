# =========================
# Imports and Configuration
# =========================
import logging
import os
import json
import re
import random
import html
import traceback
from typing import Final
import uuid
from telegram import Update, User, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext, CallbackQueryHandler, ConversationHandler, JobQueue
from telegram.constants import ChatMemberStatus

# =========================
# Logging Configuration
# =========================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
# Suppress noisy library logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Debug: Print all environment variables at startup
logger.debug(f"Environment variables: {os.environ}")

# Load the Telegram bot token from environment variable
TOKEN = os.environ.get('TELEGRAM_TOKEN')
BOT_USERNAME: Final = '@MasterBeanoBot'  # Bot's username (update if needed)

# File paths for persistent data storage
HASHTAG_DATA_FILE = 'hashtag_data.json'  # Stores hashtagged messages/media
ADMIN_DATA_FILE = 'admins.json'          # Stores admin/owner info
from functools import wraps
OWNER_ID = 7237569475  # Your Telegram ID (change to your actual Telegram user ID)


# =========================
# Decorators
# =========================
def command_handler_wrapper(admin_only=False):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            # Do not process if the message is not from a user
            if not update.effective_user or not update.message:
                return

            user = update.effective_user
            chat = update.effective_chat
            message_id = update.message.message_id

            # Defer message deletion to the end
            should_delete = True

            try:
                # Check if the command is disabled
                if chat.type in ['group', 'supergroup']:
                    command_name = func.__name__.replace('_command', '')
                    disabled_cmds = set(load_disabled_commands().get(str(chat.id), []))
                    if command_name in disabled_cmds:
                        logger.info(f"Command '{command_name}' is disabled in group {chat.id}. Aborting.")
                        return # Silently abort if command is disabled

                if admin_only and chat.type in ['group', 'supergroup']:
                    member = await context.bot.get_chat_member(chat.id, user.id)
                    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                        await update.message.reply_text(
                            f"Warning: {user.mention_html()}, you are not authorized to use this command.",
                            parse_mode='HTML'
                        )
                        # Still delete their command attempt
                        return

                # Execute the actual command function
                await func(update, context, *args, **kwargs)

            finally:
                # Delete the command message
                if should_delete and chat.type in ['group', 'supergroup']:
                    try:
                        await context.bot.delete_message(chat.id, message_id)
                    except Exception:
                        logger.warning(f"Failed to delete command message {message_id} in chat {chat.id}. Bot may not have delete permissions.")

        return wrapper
    return decorator


# =============================
# Admin/Owner Data Management
# =============================
ADMIN_NICKNAMES_FILE = 'admin_nicknames.json'

def load_admin_nicknames():
    if os.path.exists(ADMIN_NICKNAMES_FILE):
        with open(ADMIN_NICKNAMES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_admin_nicknames(data):
    with open(ADMIN_NICKNAMES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@command_handler_wrapper(admin_only=True)
async def setnickname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Only the owner can use this command.")
        return

    target_id = None
    nickname = ""

    reply_message = update.message.reply_to_message
    if reply_message:
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: Reply to a message with `/setnickname <nickname>`")
            return
        target_id = reply_message.from_user.id
        nickname = " ".join(context.args)
    else:
        if len(context.args) < 2:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /setnickname <@username or user_id> <nickname>")
            return

        target_identifier = context.args[0]
        nickname = " ".join(context.args[1:])

        if target_identifier.isdigit():
            target_id = int(target_identifier)
        else:
            target_id = await get_user_id_by_username(context, update.effective_chat.id, target_identifier)

    if not target_id:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Could not find user.")
        return

    nicknames = load_admin_nicknames()
    nicknames[str(target_id)] = nickname
    save_admin_nicknames(nicknames)

    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, target_id)
        target_user_info = member.user.mention_html()
    except Exception:
        target_user_info = f"user with ID {target_id}"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Nickname for {target_user_info} has been set to '{nickname}'.", parse_mode='HTML')

@command_handler_wrapper(admin_only=True)
async def removenickname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Only the owner can use this command.")
        return

    target_id = None

    reply_message = update.message.reply_to_message
    if reply_message:
        target_id = reply_message.from_user.id
    else:
        if not context.args:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /removenickname <@username or user_id> OR reply to a message with /removenickname")
            return

        target_identifier = context.args[0]
        if target_identifier.isdigit():
            target_id = int(target_identifier)
        else:
            target_id = await get_user_id_by_username(context, update.effective_chat.id, target_identifier)

    if not target_id:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Could not find user.")
        return

    nicknames = load_admin_nicknames()
    if str(target_id) in nicknames:
        del nicknames[str(target_id)]
        save_admin_nicknames(nicknames)

        try:
            member = await context.bot.get_chat_member(update.effective_chat.id, target_id)
            target_user_info = member.user.mention_html()
        except Exception:
            target_user_info = f"user with ID {target_id}"

        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Nickname for {target_user_info} has been removed.", parse_mode='HTML')
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This user does not have a nickname set.")


@command_handler_wrapper(admin_only=True)
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /update (admin only): Scans the group for admins and updates the global admin list.
    """
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in a group chat.")
        return

    group_id = str(chat.id)
    logger.info(f"Running /update command in group {group_id}...")

    # Get current admins from Telegram
    try:
        current_admins = await context.bot.get_chat_administrators(chat.id)
        current_admin_ids = {str(admin.user.id) for admin in current_admins}
        logger.debug(f"Current admins in group {group_id}: {current_admin_ids}")
    except Exception as e:
        logger.error(f"Failed to get admins for group {group_id}: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Error: Could not retrieve the list of administrators for this group.")
        return

    # Load existing admin data
    admin_data = load_admin_data()

    # Find users who were admin in this group but are no longer
    removed_admins = []
    for user_id, groups in list(admin_data.items()): # Use list to allow modification during iteration
        if group_id in groups and user_id not in current_admin_ids:
            groups.remove(group_id)
            removed_admins.append(user_id)
            logger.info(f"User {user_id} is no longer an admin in group {group_id}.")

    # Add new admins
    added_admins = []
    for user_id in current_admin_ids:
        if user_id not in admin_data:
            admin_data[user_id] = [group_id]
            added_admins.append(user_id)
            logger.info(f"User {user_id} is a new global admin, added from group {group_id}.")
        elif group_id not in admin_data[user_id]:
            admin_data[user_id].append(group_id)
            added_admins.append(user_id)
            logger.info(f"User {user_id} is now also an admin in group {group_id}.")

    # Save the updated data
    save_admin_data(admin_data)

    # Build and send confirmation message
    message = "✅ Admin list updated for this group.\\n"
    if added_admins:
        message += f"➕ Added {len(added_admins)} admin(s).\\n"
    if removed_admins:
        message += f"➖ Removed {len(removed_admins)} admin(s).\\n"

    await context.bot.send_message(chat_id=update.effective_chat.id, text=message)


def load_admin_data():
    """Load admin data from file."""
    if os.path.exists(ADMIN_DATA_FILE):
        with open(ADMIN_DATA_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not isinstance(data, dict):
                    logger.warning("Admin data file is not a dictionary, returning empty.")
                    return {}
                return data
            except json.JSONDecodeError:
                logger.warning("Failed to decode admin data file, returning empty.")
                return {}
    return {}

def save_admin_data(data):
    """Save admin data to file."""
    with open(ADMIN_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug(f"Saved admin data: {data}")

def is_owner(user_id):
    """Check if the user is the owner."""
    return str(user_id) == str(OWNER_ID)

def get_display_name(user_id: int, full_name: str) -> str:
    """
    Determines the display name for a user.
    It prioritizes nicknames, then falls back to the user's full name.
    """
    nicknames = load_admin_nicknames()
    name = nicknames.get(str(user_id))
    if name:
        return name

    # Fallback to the user's full name, safely escaped.
    return html.escape(full_name)

def get_capitalized_name(user_id: int, full_name: str) -> str:
    """
    Gets the user's display name and capitalizes it.
    """
    name = get_display_name(user_id, full_name)
    return name.capitalize()

def is_admin(user_id):
    """Check if the user is the owner or an admin in any group."""
    if is_owner(user_id):
        return True
    data = load_admin_data()
    user_id_str = str(user_id)
    # Check if user_id is a key and has a non-empty list of groups
    is_admin_result = user_id_str in data and isinstance(data.get(user_id_str), list) and len(data[user_id_str]) > 0
    logger.debug(f"is_admin({user_id}) -> {is_admin_result}")
    return is_admin_result

async def get_user_id_by_username(context, chat_id, username) -> str:
    """Get a user's Telegram ID by their username in a chat."""
    async for member in await context.bot.get_chat_administrators(chat_id):
        if member.user.username and member.user.username.lower() == username.lower().lstrip('@'):
            logger.debug(f"Found user ID {member.user.id} for username {username}")
            return str(member.user.id)
    logger.debug(f"Username {username} not found in chat {chat_id}")
    return None

# =============================
# Hashtag Data Management
# =============================
def load_hashtag_data():
    """Load hashtagged message/media data from file."""
    if os.path.exists(HASHTAG_DATA_FILE):
        with open(HASHTAG_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            logger.debug(f"Loaded hashtag data: {list(data.keys())}")
            return data
    logger.debug("No hashtag data file found, returning empty dict.")
    return {}

def save_hashtag_data(data):
    """Save hashtagged message/media data to file."""
    with open(HASHTAG_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug(f"Saved hashtag data: {list(data.keys())}")

import asyncio
import time


# =============================
# Inactivity Tracking & Settings
# =============================
ACTIVITY_DATA_FILE = 'activity.json'  # Tracks last activity per user per group
INACTIVE_SETTINGS_FILE = 'inactive_settings.json'  # Stores inactivity threshold per group

def load_activity_data():
    if os.path.exists(ACTIVITY_DATA_FILE):
        with open(ACTIVITY_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_activity_data(data):
    with open(ACTIVITY_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_inactive_settings():
    if os.path.exists(INACTIVE_SETTINGS_FILE):
        with open(INACTIVE_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_inactive_settings(data):
    with open(INACTIVE_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_user_activity(user_id, group_id):
    data = load_activity_data()
    group_id = str(group_id)
    user_id = str(user_id)
    if group_id not in data:
        data[group_id] = {}
    data[group_id][user_id] = int(time.time())
    save_activity_data(data)
    logger.debug(f"Updated activity for user {user_id} in group {group_id}")

# =============================
# Hashtag Message Handler
# =============================
async def hashtag_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles messages containing hashtags, saving them (and any media) for later retrieval.
    Supports both single messages and media groups.
    Also updates user activity for inactivity tracking.
    """
    message = update.message or update.edited_message
    if not message:
        logger.debug("No message found in update for hashtag handler.")
        return
    # Update user activity for inactivity tracking
    if message.chat and message.from_user and message.chat.type in ["group", "supergroup"]:
        update_user_activity(message.from_user.id, message.chat.id)
    text = message.text or message.caption or ''
    hashtags = re.findall(r'#(\w+)', text)
    if not hashtags:
        logger.debug("No hashtags found in message.")
        return

    # Handle single media or text
    data = load_hashtag_data()
    for tag in hashtags:
        tag = tag.lower()
        entry = {
            'user_id': message.from_user.id,
            'username': message.from_user.username,
            'text': message.text if message.text else None,
            'caption': message.caption if message.caption else None,
            'message_id': message.message_id,
            'chat_id': message.chat.id,
            'media_group_id': None,
            'photos': [],
            'videos': []
        }
        if message.photo:
            entry['photos'] = [message.photo[-1].file_id]
        if message.video:
            entry['videos'] = [message.video.file_id]
        if message.document and message.document.mime_type and message.document.mime_type.startswith('video'):
            entry['videos'].append(message.document.file_id)
        data.setdefault(tag, []).append(entry)
        logger.debug(f"Saved single message under tag #{tag}")
    save_hashtag_data(data)

    # Notify admins privately
    admins = await context.bot.get_chat_administrators(message.chat.id)
    notification_text = (
        f"A new post from {message.from_user.mention_html()} in group {message.chat.title} "
        f"has been saved with the tag(s): {', '.join('#'+t for t in hashtags)}"
    )
    for admin in admins:
        try:
            await context.bot.send_message(chat_id=admin.user.id, text=notification_text, parse_mode='HTML')
        except Exception:
            logger.warning(f"Failed to notify admin {admin.user.id} about new hashtagged post.")

# =============================
# Dynamic Hashtag Command Handler
# =============================
async def dynamic_hashtag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles dynamic hashtag commands (e.g. /mytag) to retrieve saved messages/media.
    This acts as a fallback for any command not in COMMAND_MAP.
    """
    if update.effective_chat.type == "private":
        # This message is not sent because the wrapper deletes the command.
        # It's better to handle this check inside the command logic if a response is needed.
        return

    if not update.message or not update.message.text:
        return

    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        return  # Silently ignore for non-admins

    command = update.message.text[1:].split()[0].lower()

    # Prevent this handler from hijacking static commands defined in COMMAND_MAP
    if command in COMMAND_MAP:
        return

    data = load_hashtag_data()
    if command not in data:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No data found for #{command}.")
        logger.debug(f"No data found for command: {command}")
        return
    # No admin check: allow all users to use hashtag commands
    found = False
    for entry in data[command]:
        # Send all photos
        for photo_id in entry.get('photos', []):
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption=entry.get('caption') or entry.get('text') or '')
            found = True
        # Send all videos
        for video_id in entry.get('videos', []):
            await context.bot.send_video(chat_id=update.effective_chat.id, video=video_id, caption=entry.get('caption') or entry.get('text') or '')
            found = True
        # Fallback for text/caption only
        if not entry.get('photos') and not entry.get('videos') and (entry.get('text') or entry.get('caption')):
            await context.bot.send_message(chat_id=update.effective_chat.id, text=entry.get('text') or entry.get('caption'))
            found = True
    if not found:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No saved messages or photos for #{command}.")
        logger.debug(f"No saved messages or media for command: {command}")

# =============================
# /command - List all commands
# =============================
COMMAND_MAP = {
    'start': {'is_admin': False}, 'help': {'is_admin': False}, 'beowned': {'is_admin': False},
    'command': {'is_admin': False}, 'disable': {'is_admin': True}, 'admin': {'is_admin': False},
    'link': {'is_admin': True}, 'inactive': {'is_admin': True}, 'setnickname': {'is_admin': True},
    'removenickname': {'is_admin': True}, 'enable': {'is_admin': True}, 'update': {'is_admin': True},
}

@command_handler_wrapper(admin_only=False)
async def command_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dynamically lists all available commands based on user's admin status and disabled commands.
    """
    if update.effective_chat.type == "private":
        await update.message.reply_text("Please use this command in a group to see the available commands for that group.")
        return

    group_id = str(update.effective_chat.id)
    disabled_cmds = set(load_disabled_commands().get(group_id, []))

    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    is_admin_user = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]

    everyone_cmds = []
    admin_only_cmds = []

    # Static commands from COMMAND_MAP
    for cmd, info in sorted(COMMAND_MAP.items()):
        if cmd in ['start', 'help']:  # Don't show these in the group list
            continue

        is_disabled = cmd in disabled_cmds
        display_cmd = f"/{cmd}"
        if is_disabled:
            display_cmd += " (disabled)"

        if info['is_admin']:
            if is_admin_user:  # Admins see all admin commands
                admin_only_cmds.append(display_cmd)
        else:  # Everyone commands
            if not is_disabled:
                everyone_cmds.append(display_cmd)
            elif is_admin_user:  # Admins also see disabled everyone commands
                everyone_cmds.append(display_cmd)

    # Dynamic hashtag commands (always admin-only)
    if is_admin_user:
        hashtag_data = load_hashtag_data()
        for tag in sorted(hashtag_data.keys()):
            admin_only_cmds.append(f"/{tag}")

    msg = '<b>Commands for everyone:</b>\n' + ('\n'.join(everyone_cmds) if everyone_cmds else 'None')
    if is_admin_user:
        msg += '\n\n<b>Commands for admins only:</b>\n' + ('\n'.join(admin_only_cmds) if admin_only_cmds else 'None')

    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='HTML')

# Persistent storage for disabled commands per group
DISABLED_COMMANDS_FILE = 'disabled_commands.json'

def load_disabled_commands():
    if os.path.exists(DISABLED_COMMANDS_FILE):
        with open(DISABLED_COMMANDS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_disabled_commands(data):
    with open(DISABLED_COMMANDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# /disable - Remove a dynamic hashtag command or disable a static command (admin only)
@command_handler_wrapper(admin_only=True)
async def disable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Update user activity for inactivity tracking
    if update.effective_user and update.effective_chat and update.effective_chat.type in ["group", "supergroup"]:
        update_user_activity(update.effective_user.id, update.effective_chat.id)
    if update.effective_chat.type == "private":
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in group chats.")
        return
    if not update.message or not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /disable <command or hashtag>")
        return
    tag = context.args[0].lstrip('#/').lower()
    data = load_hashtag_data()
    # Dynamic command removal
    if tag in data:
        del data[tag]
        save_hashtag_data(data)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Dynamic command /{tag} has been disabled.")
        return
    # Static command disabling
    if tag in COMMAND_MAP:
        group_id = str(update.effective_chat.id)
        disabled = load_disabled_commands()
        disabled.setdefault(group_id, [])
        if tag not in disabled[group_id]:
            disabled[group_id].append(tag)
            save_disabled_commands(disabled)
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{tag} has been disabled in this group. Admins can re-enable it with /enable {tag}.")
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{tag} is already disabled.")
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No such dynamic or static command: /{tag}")

@command_handler_wrapper(admin_only=True)
async def enable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /enable <command> (admin only): Enables a previously disabled command in the group.
    """
    if update.effective_chat.type not in ["group", "supergroup"]:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in group chats.")
        return
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /enable <command>")
        return

    command_to_enable = context.args[0].lstrip('/').lower()
    group_id = str(update.effective_chat.id)
    disabled = load_disabled_commands()

    if group_id in disabled and command_to_enable in disabled[group_id]:
        disabled[group_id].remove(command_to_enable)
        if not disabled[group_id]:  # Remove group key if list is empty
            del disabled[group_id]
        save_disabled_commands(disabled)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{command_to_enable} has been enabled in this group.")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{command_to_enable} is not currently disabled.")

@command_handler_wrapper(admin_only=False)
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /admin (as a reply): Forwards a message to the group admins for review.
    """
    message = update.message
    chat = update.effective_chat

    if chat.type not in ['group', 'supergroup']:
        await context.bot.send_message(chat_id=chat.id, text="This command can only be used in group chats.")
        return

    if not message.reply_to_message:
        await context.bot.send_message(chat_id=chat.id, text="Please use this command as a reply to the message you want to report.")
        return

    # Update user activity
    if update.effective_user:
        update_user_activity(update.effective_user.id, chat.id)

    # Prepare the report
    reporting_user = update.effective_user
    reported_message = message.reply_to_message
    reported_user = reported_message.from_user
    reason = " ".join(context.args) if context.args else "No reason provided."

    # Use the new get_display_name for respectful naming
    reporting_user_display = get_display_name(reporting_user.id, reporting_user.full_name)
    reported_user_display = get_display_name(reported_user.id, reported_user.full_name)

    # Create a link to the message
    message_link = f"https://t.me/c/{str(chat.id).replace('-100', '')}/{reported_message.message_id}"

    report_text = (
        f"🚨 <b>Admin Report</b> 🚨\n\n"
        f"<b>Group:</b> {html.escape(chat.title)}\n"
        f"<b>Reported by:</b> {reporting_user_display}\n"
        f"<b>Reported user:</b> {reported_user_display}\n"
        f"<b>Reason:</b> {html.escape(reason)}\n\n"
        f"<a href='{message_link}'>Go to message</a>"
    )

    # Notify admins
    admins = await context.bot.get_chat_administrators(chat.id)
    notification_sent = False
    for admin in admins:
        # Don't notify the bot itself if it's an admin
        if admin.user.is_bot:
            continue
        try:
            # Forward the original message first
            await context.bot.forward_message(
                chat_id=admin.user.id,
                from_chat_id=chat.id,
                message_id=reported_message.message_id
            )
            # Then send the report context
            await context.bot.send_message(
                chat_id=admin.user.id,
                text=report_text,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            notification_sent = True
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin.user.id} for report in group {chat.id}: {e}")

    if notification_sent:
        # Confirm to the user that the report was sent
        confirmation_msg = await message.reply_text("The admins have been notified.")
        # Delete the confirmation message after a delay
        context.job_queue.run_once(
            delete_message_callback,
            30,
            chat_id=confirmation_msg.chat_id,
            data=confirmation_msg.message_id,
            name=f"delete_confirm_{confirmation_msg.message_id}"
        )
    else:
        await message.reply_text("Could not notify any admins. Please ensure the bot has the correct permissions.")


@command_handler_wrapper(admin_only=True)
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /link (admin only): Creates a single-use invite link for the group.
    """
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == 'private':
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This command is used to generate an invite link for a group. Please run this command inside the group you want the link for.")
        return

    if chat.type in ['group', 'supergroup']:
        try:
            # Create a single-use invite link
            invite_link = await context.bot.create_chat_invite_link(
                chat_id=chat.id,
                member_limit=1,
                name=f"Invite for {user.full_name}"
            )

            # Send the link to the admin in a private message
            try:
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"Here is your single-use invite link for the group '{chat.title}':\n{invite_link.invite_link}"
                )
                # Confirm in the group chat
                await context.bot.send_message(chat_id=update.effective_chat.id, text="I have sent you a single-use invite link in a private message.")
            except Exception as e:
                logger.error(f"Failed to send private message to admin {user.id}: {e}")
                await context.bot.send_message(chat_id=update.effective_chat.id, text="I couldn't send you a private message. Please make sure you have started a chat with me privately first.")

        except Exception as e:
            logger.error(f"Failed to create invite link for chat {chat.id}: {e}")
            await context.bot.send_message(chat_id=update.effective_chat.id, text="I was unable to create an invite link. Please ensure I have the 'Invite Users via Link' permission in this group.")


#Start command
@command_handler_wrapper(admin_only=False)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].startswith('setstake_'):
        return  # This is handled by the game setup conversation handler

    # Update user activity for inactivity tracking
    if update.effective_user and update.effective_chat and update.effective_chat.type in ["group", "supergroup"]:
        update_user_activity(update.effective_user.id, update.effective_chat.id)

    user_mention = update.effective_user.mention_html()
    start_message = f"Hey there {user_mention}! What can I help you with?"

    if update.effective_chat.type != "private":
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please message me in private to use /start.")
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=start_message,
                parse_mode='HTML'
            )
        except Exception:
            logger.warning(f"Failed to send private start message to {update.effective_user.id}")
        return

    # Check if disabled in this group (should never trigger in private)
    group_id = str(update.effective_chat.id)
    disabled = load_disabled_commands()
    if 'start' in disabled.get(group_id, []):
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text=start_message, parse_mode='HTML')

#Help command
@command_handler_wrapper(admin_only=False)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the interactive help menu.
    """
    if update.effective_chat.type != "private":
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please use the /help command in a private chat with me for a better experience.")
        return

    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("General Commands", callback_data='help_general')]
    ]

    # Only show Admin Commands button to admins
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("Admin Commands", callback_data='help_admin')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the help menu! Please choose a category:",
        reply_markup=reply_markup
    )


async def help_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all interactions with the interactive help menu."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    topic = query.data

    text = ""
    keyboard = [[InlineKeyboardButton("« Back to Main Menu", callback_data='help_back')]]

    if topic == 'help_general':
        text = """
<b>General Commands</b>
- /help: Shows this help menu.
- /command: Lists all available commands in the current group.
- /beowned: Information on how to be owned.
- /admin: Request help from admins in a group.
        """
    elif topic == 'help_admin':
        if not is_admin(user_id):
            await query.answer("You are not authorized to view this section.", show_alert=True)
            return

        admin_cmds = [f"/{cmd}" for cmd, info in sorted(COMMAND_MAP.items()) if info['is_admin']]

        hashtag_data = load_hashtag_data()
        if hashtag_data:
            admin_cmds.extend(f"/{tag}" for tag in sorted(hashtag_data.keys()))

        text = "<b>Admin Commands</b>\n"
        text += "These commands are available to you in groups where you are an admin:\n\n"
        text += '\n'.join(admin_cmds)
        text += "\n\n<i>Note: Dynamic hashtag commands (if any are listed) can be removed with /disable.</i>"

    elif topic == 'help_back':
        main_menu_keyboard = [
            [InlineKeyboardButton("General Commands", callback_data='help_general')]
        ]
        if is_admin(user_id):
            main_menu_keyboard.append([InlineKeyboardButton("Admin Commands", callback_data='help_admin')])

        await query.edit_message_text(
            "Welcome to the help menu! Please choose a category:",
            reply_markup=InlineKeyboardMarkup(main_menu_keyboard)
        )
        return

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML', disable_web_page_preview=True)

#BeOwned command
@command_handler_wrapper(admin_only=False)
async def beowned_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Update user activity for inactivity tracking
    if update.effective_user and update.effective_chat and update.effective_chat.type in ["group", "supergroup"]:
        update_user_activity(update.effective_user.id, update.effective_chat.id)
    # Check if disabled in this group
    if update.effective_chat.type != "private":
        group_id = str(update.effective_chat.id)
        disabled = load_disabled_commands()
        if 'beowned' in disabled.get(group_id, []):
            return
    await context.bot.send_message(chat_id=update.effective_chat.id, text="If you want to be Lion's property, contact @Lionspridechatbot with a head to toe nude picture of yourself and a clear, concise and complete presentation of yourself.")

#Responses
def handle_response(text: str) -> str:
    processed: str = text.lower()
    if 'dog' in processed:
        return 'Is @Luke082 here? Someone should use his command (/luke8)!'

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.edited_message
    if not message:
        return

    # Update user activity for inactivity tracking
    if message.from_user and message.chat and message.chat.type in ["group", "supergroup"]:
        update_user_activity(message.from_user.id, message.chat.id)
    if message.text:
        response = handle_response(message.text)
        if response:
            await message.reply_text(response)

import html
import traceback

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    # Log the error before we do anything else, so we can see it even if something breaks.
    logger.error("Exception while handling an update:", exc_info=context.error)

    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = (
        f"An exception was raised while handling an update\n"
        f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n\n"
        f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
        f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
        f"<pre>{html.escape(tb_string)}</pre>"
    )

    logger.error(message)



# =============================
# Timed Message Deletion
# =============================
async def delete_message_callback(context: CallbackContext):
    """Deletes the message specified in the job context."""
    try:
        await context.bot.delete_message(chat_id=context.job.chat_id, message_id=context.job.data)
        logger.debug(f"Deleted scheduled message {context.job.data} in chat {context.job.chat_id}")
    except Exception as e:
        logger.warning(f"Failed to delete scheduled message: {e}")


# =============================
# /inactive command and auto-kick logic
# =============================
@command_handler_wrapper(admin_only=True)
async def inactive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /inactive <days> (admin only):
    - /inactive 0 disables auto-kick in the group.
    - /inactive <n> (1-99) enables auto-kick for users inactive for n days.
    """
    if update.effective_chat.type not in ["group", "supergroup"]:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in group chats.")
        return
    if not context.args or not context.args[0].strip().isdigit():
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /inactive <days> (0 to disable, 1-99 to enable)")
        return
    days = int(context.args[0].strip())
    group_id = str(update.effective_chat.id)
    settings = load_inactive_settings()
    if days == 0:
        settings.pop(group_id, None)
        save_inactive_settings(settings)
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Inactive user kicking is now disabled in this group.")
        logger.debug(f"Inactive kicking disabled for group {group_id}")
        return
    if not (1 <= days <= 99):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please provide a number of days between 1 and 99.")
        return
    settings[group_id] = days
    save_inactive_settings(settings)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Inactive user kicking is now enabled for this group. Users inactive for {days} days will be kicked.")
    logger.debug(f"Inactive kicking enabled for group {group_id} with threshold {days} days")

async def check_and_kick_inactive_users(app):
    """
    Checks all groups with inactivity kicking enabled and kicks users who have been inactive too long.
    """
    logger.debug("Running periodic inactive user check...")
    settings = load_inactive_settings()
    activity = load_activity_data()
    now = int(time.time())
    for group_id, days in settings.items():
        group_activity = activity.get(group_id, {})
        threshold = now - days * 86400
        try:
            bot = app.bot
            admins = await bot.get_chat_administrators(int(group_id))
            admin_ids = {str(admin.user.id) for admin in admins}
            members = list(group_activity.keys())
            for user_id in members:
                if user_id in admin_ids:
                    continue  # Never kick admins
                last_active = group_activity.get(user_id, 0)
                if last_active < threshold:
                    try:
                        await bot.ban_chat_member(int(group_id), int(user_id))
                        await bot.unban_chat_member(int(group_id), int(user_id))  # Unban to allow rejoining
                        print(f"[DEBUG] Kicked inactive user {user_id} from group {group_id}")
                    except Exception as e:
                        logger.error(f"Failed to kick user {user_id} from group {group_id}: {e}")
        except Exception as e:
            logger.error(f"Failed to process group {group_id} for inactivity kicking: {e}")

# =============================
# Command Registration Helper
# =============================
def add_command(app: Application, command: str, handler):
    """
    Registers a command with support for /, ., and ! prefixes.
    """
    # Wrapper for MessageHandlers to populate context.args
    async def message_handler_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and update.message.text:
            context.args = update.message.text.split()[1:]
        await handler(update, context)

    # Register for /<command> - uses the original handler as it populates args automatically
    app.add_handler(CommandHandler(command, handler))

    # Register for .<command> and !<command> - uses the wrapper
    app.add_handler(MessageHandler(filters.Regex(rf'^\.{command}(\s|$)'), message_handler_wrapper))
    app.add_handler(MessageHandler(filters.Regex(rf'^!{command}(\s|$)'), message_handler_wrapper))


if __name__ == '__main__':
    logger.info('Starting Telegram Bot...')
    logger.debug(f'TOKEN value: {TOKEN}')
    # Define post-init function to start periodic task after event loop is running
    async def periodic_inactive_check_job(context: ContextTypes.DEFAULT_TYPE):
        await check_and_kick_inactive_users(context.application)

    async def on_startup(app):
        # Schedule the periodic job using the job queue (every hour)
        app.job_queue.run_repeating(periodic_inactive_check_job, interval=3600, first=10)

    job_queue = JobQueue()
    app = Application.builder().token(TOKEN).post_init(on_startup).job_queue(job_queue).build()

    #Commands
    # Register all commands using the new helper
    add_command(app, 'start', start_command)
    add_command(app, 'help', help_command)
    add_command(app, 'beowned', beowned_command)
    add_command(app, 'command', command_list_command)
    add_command(app, 'disable', disable_command)
    add_command(app, 'admin', admin_command)
    add_command(app, 'link', link_command)
    add_command(app, 'inactive', inactive_command)
    add_command(app, 'setnickname', setnickname_command)
    add_command(app, 'removenickname', removenickname_command)
    add_command(app, 'enable', enable_command)
    add_command(app, 'update', update_command)

    app.add_handler(CallbackQueryHandler(help_menu_handler, pattern=r'^help_'))

    # Fallback handler for dynamic hashtag commands.
    # The group=1 makes it lower priority than the static commands registered with add_command (which are in the default group 0)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^[./!].*'), dynamic_hashtag_command), group=1)

    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION | filters.ATTACHMENT) & ~filters.COMMAND, hashtag_message_handler))
    # Unified handler for edited messages: process hashtags, responses, and future logic
    async def edited_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Route edited messages through all main logic
        await hashtag_message_handler(update, context)
        await message_handler(update, context)
        # Add future logic here as needed
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message_handler))
    app.add_handler(MessageHandler(filters.TEXT, message_handler))

    # Errors
    app.add_error_handler(error_handler)

    #Check for updates
    logger.info('Polling...')
    app.run_polling(poll_interval=0.5)