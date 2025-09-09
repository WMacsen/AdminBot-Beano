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
from pathlib import Path
import asyncio
from functools import wraps
from telegram import Update, User, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext, CallbackQueryHandler, ConversationHandler, JobQueue
from telegram.constants import ChatMemberStatus
from dotenv import load_dotenv

# Get the absolute path of the directory where the script is located
BASE_DIR = Path(__file__).resolve().parent

# Create locks for file access
FILE_LOCKS = {
    "risk": asyncio.Lock(),
    "nicknames": asyncio.Lock(),
    "admins": asyncio.Lock(),
    "hashtags": asyncio.Lock(),
    "activity": asyncio.Lock(),
    "inactive": asyncio.Lock(),
    "disabled": asyncio.Lock(),
}

# =========================
# Logging Configuration
# =========================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(BASE_DIR / "bot.log", encoding='utf-8'),
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
load_dotenv()
TOKEN = os.environ.get('TELEGRAM_TOKEN')
BOT_USERNAME: Final = '@MasterBeanoBot'  # Bot's username (update if needed)

# File paths for persistent data storage
HASHTAG_DATA_FILE = BASE_DIR / 'hashtag_data.json'
ADMIN_DATA_FILE = BASE_DIR / 'admins.json'
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
ADMIN_NICKNAMES_FILE = BASE_DIR / 'admin_nicknames.json'
RISK_DATA_FILE = BASE_DIR / 'risk_data.json'
CONDITIONS_DATA_FILE = BASE_DIR / 'conditions.json'

def load_risk_data():
    if os.path.exists(RISK_DATA_FILE):
        with open(RISK_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_risk_data(data):
    with open(RISK_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_conditions_data():
    if os.path.exists(CONDITIONS_DATA_FILE):
        with open(CONDITIONS_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_conditions_data(data):
    with open(CONDITIONS_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
        if len(context.args) < 2 or not context.args[0].isdigit():
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: `/setnickname <user_id> <nickname>` or reply to a user's message.")
            return

        target_id = int(context.args[0])
        nickname = " ".join(context.args[1:])

    if not target_id:
        # This case is unlikely to be reached now but serves as a safeguard.
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Could not identify the target user.")
        return

    nicknames = load_admin_nicknames()
    nicknames[str(target_id)] = nickname
    save_admin_nicknames(nicknames)

    target_user_info = f"user with ID {target_id}"
    try:
        if update.effective_chat.type != 'private':
            member = await context.bot.get_chat_member(update.effective_chat.id, target_id)
            target_user_info = member.user.mention_html()
    except Exception:
        # Fallback to user ID if we can't get chat member info
        pass

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
        if not context.args or not context.args[0].isdigit():
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: Reply to a user with /removenickname, or use `/removenickname <user_id>`.")
            return
        target_id = int(context.args[0])

    if not target_id:
        # This case is unlikely to be reached now but serves as a safeguard.
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Could not identify the target user.")
        return

    nicknames = load_admin_nicknames()
    if str(target_id) in nicknames:
        del nicknames[str(target_id)]
        save_admin_nicknames(nicknames)

        target_user_info = f"user with ID {target_id}"
        try:
            if update.effective_chat.type != 'private':
                member = await context.bot.get_chat_member(update.effective_chat.id, target_id)
                target_user_info = member.user.mention_html()
        except Exception:
            # Fallback to user ID if we can't get chat member info
            pass

        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Nickname for {target_user_info} has been removed.", parse_mode='HTML')
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="This user does not have a nickname set.")


@command_handler_wrapper(admin_only=True)
async def allban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Bans a user from all groups the bot is in, unless the command is disabled in a group.
    Can be used with user ID, @username, or by replying to a message.
    """
    target_user_id = None
    target_user_info = "the user"

    if update.message.reply_to_message:
        target_user_id = update.message.reply_to_message.from_user.id
        target_user_info = update.message.reply_to_message.from_user.mention_html()
    elif context.args:
        arg = context.args[0]
        if arg.isdigit():
            target_user_id = int(arg)
            try:
                user = await context.bot.get_chat(target_user_id)
                target_user_info = user.mention_html()
            except Exception:
                target_user_info = f"user with ID `{target_user_id}`"
        elif arg.startswith('@'):
            username_to_find = arg[1:].lower()
            risk_data = load_risk_data()
            found_user_id = None
            for user_id_str, risks in risk_data.items():
                if any(r.get('username', '').lower() == username_to_find for r in risks):
                    found_user_id = user_id_str
                    break
            if found_user_id:
                target_user_id = int(found_user_id)
                target_user_info = f"user @{username_to_find}"
            else:
                await update.message.reply_text(f"Could not find a user ID for {arg}. This can happen if I haven't interacted with them before. Please use their user ID or reply to one of their messages.")
                return
        else:
            await update.message.reply_text("Invalid argument. Please provide a user ID, a @username, or reply to a user's message.")
            return
    else:
        await update.message.reply_text("Usage: /allban <user_id or @username> or reply to a user's message.")
        return

    if not target_user_id:
        await update.message.reply_text("Could not identify the target user.")
        return

    if is_owner(target_user_id):
        await update.message.reply_text("You cannot ban the owner.")
        return
    if target_user_id == update.effective_user.id:
        await update.message.reply_text("You cannot ban yourself.")
        return

    admin_data = load_admin_data()
    all_group_ids = {group for groups in admin_data.values() for group in groups}
    disabled_cmds = load_disabled_commands()

    successful_bans = []
    failed_bans = []

    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Processing all-ban for {target_user_info}. This may take a moment...", parse_mode='HTML')

    for group_id in all_group_ids:
        if 'allban' in disabled_cmds.get(str(group_id), []):
            continue

        try:
            await context.bot.ban_chat_member(chat_id=int(group_id), user_id=target_user_id)
            try:
                chat = await context.bot.get_chat(int(group_id))
                successful_bans.append(html.escape(chat.title))
            except Exception:
                successful_bans.append(f"Group ID {group_id}")
        except Exception as e:
            try:
                chat = await context.bot.get_chat(int(group_id))
                failed_bans.append(f"{html.escape(chat.title)} (Reason: {e})")
            except Exception:
                failed_bans.append(f"Group ID {group_id} (Reason: {e})")

    summary_message = f"All-ban executed for {target_user_info}.\n\n"
    if successful_bans:
        summary_message += f"✅ <b>Successfully banned from:</b>\n- " + "\n- ".join(successful_bans)
    if failed_bans:
        summary_message += f"\n\n❌ <b>Failed to ban from:</b>\n- " + "\n- ".join(failed_bans)
    if not successful_bans and not failed_bans:
        summary_message = f"Could not perform the ban. Either the bot is not in any groups or the `/allban` command is disabled in all of them."

    await context.bot.send_message(chat_id=update.effective_chat.id, text=summary_message, parse_mode='HTML')


@command_handler_wrapper(admin_only=True)
async def addcondition_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        return

    if not context.args:
        await context.bot.send_message(chat_id=chat.id, text="Usage: /addcondition <text of the condition>")
        return

    condition_text = " ".join(context.args)
    group_id = str(chat.id)

    conditions_data = load_conditions_data()
    if not isinstance(conditions_data, dict):
        # If the old data is a list, we start fresh to avoid data corruption.
        # A more sophisticated migration could be implemented if needed.
        conditions_data = {}

    group_conditions = conditions_data.get(group_id, [])

    new_condition = {
        'id': uuid.uuid4().hex[:5],
        'text': condition_text
    }
    group_conditions.append(new_condition)
    conditions_data[group_id] = group_conditions
    save_conditions_data(conditions_data)

    await context.bot.send_message(chat_id=chat.id, text=f"✅ Condition added with ID: `{new_condition['id']}` for this group.", parse_mode='HTML')

@command_handler_wrapper(admin_only=True)
async def listconditions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        return

    group_id = str(chat.id)
    conditions_data = load_conditions_data()

    if not isinstance(conditions_data, dict):
        # Handle case where data might be in the old list format or non-existent
        await context.bot.send_message(chat_id=chat.id, text="No conditions have been set for this group.")
        return

    group_conditions = conditions_data.get(group_id, [])

    if not group_conditions:
        await context.bot.send_message(chat_id=chat.id, text="No conditions have been set for this group.")
        return

    message = "📜 <b>Current Conditions for this Group</b>\n\n"
    for cond in group_conditions:
        message += f"- <b>ID: {cond['id']}</b>\n  <i>{html.escape(cond['text'])}</i>\n\n"

    await context.bot.send_message(chat_id=chat.id, text=message, parse_mode='HTML')


@command_handler_wrapper(admin_only=True)
async def removecondition_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        return

    if not context.args:
        await context.bot.send_message(chat_id=chat.id, text="Usage: /removecondition <condition_id>")
        return

    condition_id_to_remove = context.args[0]
    group_id = str(chat.id)
    conditions_data = load_conditions_data()

    if not isinstance(conditions_data, dict):
        # Data is not in the expected format, so there's nothing to remove.
        await context.bot.send_message(chat_id=chat.id, text=f"❌ Could not find a condition with ID `{condition_id_to_remove}` in this group.", parse_mode='HTML')
        return

    group_conditions = conditions_data.get(group_id, [])
    if not group_conditions:
        await context.bot.send_message(chat_id=chat.id, text=f"❌ Could not find a condition with ID `{condition_id_to_remove}` in this group.", parse_mode='HTML')
        return

    initial_count = len(group_conditions)
    # Filter out the condition to be removed
    group_conditions = [c for c in group_conditions if c.get('id') != condition_id_to_remove]

    if len(group_conditions) < initial_count:
        if group_conditions:
            # If there are remaining conditions, update the list for the group
            conditions_data[group_id] = group_conditions
        else:
            # If no conditions are left, remove the group entry entirely
            del conditions_data[group_id]
        save_conditions_data(conditions_data)
        await context.bot.send_message(chat_id=chat.id, text=f"✅ Condition with ID `{condition_id_to_remove}` has been removed from this group.", parse_mode='HTML')
    else:
        await context.bot.send_message(chat_id=chat.id, text=f"❌ Could not find a condition with ID `{condition_id_to_remove}` in this group.", parse_mode='HTML')


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
    for user_id, groups in list(admin_data.items()):  # Use list to allow modification during iteration
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
    message_parts = ["✅ Admin list updated for this group."]
    if added_admins:
        message_parts.append(f"➕ Added {len(added_admins)} admin(s).")
    if removed_admins:
        message_parts.append(f"➖ Removed {len(removed_admins)} admin(s).")
    if not added_admins and not removed_admins:
        message_parts.append("No changes were needed.")

    message = "\n".join(message_parts)
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
ACTIVITY_DATA_FILE = BASE_DIR / 'activity.json'
INACTIVE_SETTINGS_FILE = BASE_DIR / 'inactive_settings.json'

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
    This acts as a fallback for any command not in COMMAND_MAP. It ignores unknown commands.
    """
    if update.effective_chat.type == "private":
        return

    if not update.message or not update.message.text:
        return

    # This handler should only be triggered for admins, as per original logic.
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        return  # Silently ignore for non-admins

    # Check if the command is addressed to another bot.
    full_command_text = update.message.text[1:].split()[0]
    command_parts = full_command_text.split('@')
    if len(command_parts) > 1 and command_parts[1].lower() != BOT_USERNAME[1:].lower():
        return  # Command is for another bot, so ignore.

    command = command_parts[0].lower()

    # Prevent this handler from hijacking static commands defined in COMMAND_MAP
    if command in COMMAND_MAP:
        return

    # Check if the command is a known hashtag command. If not, silently ignore.
    data = load_hashtag_data()
    if command not in data:
        logger.debug(f"Unknown command '/{command}' not in hashtag data. Ignoring.")
        return

    # If we are here, it's a valid hashtag command from an admin.
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
        # This case might happen if a hashtag exists but has no content (e.g. empty list).
        # We should not send a message here, to be consistent with ignoring unknown commands.
        logger.debug(f"No saved messages or media for command: {command}, though tag exists.")

# =============================
# Risk Command
# =============================

# States for ConversationHandler
SELECT_GROUP, AWAIT_MEDIA, AWAIT_BEGGING = range(3)

# States for Post ConversationHandler
SELECT_POST_GROUP, AWAIT_POST_MEDIA, AWAIT_POST_CAPTION, CONFIRM_POST = range(2, 6)

# States for Purge ConversationHandler
CONFIRM_PURGE, AWAIT_CONDITION_VERIFICATION = range(6, 8)


async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the /risk conversation. Asks user to select a group."""
    if update.effective_chat.type != "private":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="The /risk command is only available in private chat."
        )
        # Attempt to start a private message instead
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="Please use the /risk command here."
            )
        except Exception:
            pass # Ignore if user has not started a chat with the bot
        return ConversationHandler.END

    admin_data = load_admin_data()
    if not admin_data:
        await update.message.reply_text("The bot is not yet configured in any groups. Please use /update in a group first.")
        return ConversationHandler.END

    all_group_ids = {group for groups in admin_data.values() for group in groups}
    disabled_data = load_disabled_commands()

    keyboard = []
    for group_id in all_group_ids:
        if 'risk' in disabled_data.get(str(group_id), []):
            continue  # Skip disabled groups

        try:
            chat = await context.bot.get_chat(int(group_id))
            keyboard.append([InlineKeyboardButton(chat.title, callback_data=f"risk_group_{group_id}")])
        except Exception as e:
            logger.warning(f"Could not fetch chat info for group {group_id}: {e}")

    if not keyboard:
        await update.message.reply_text("There are no groups available for the /risk command right now.")
        return ConversationHandler.END

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose a group where you want to risk your fate:", reply_markup=reply_markup)
    return SELECT_GROUP

async def select_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the group selection and asks for media."""
    query = update.callback_query
    await query.answer()

    group_id = query.data.replace("risk_group_", "")
    context.user_data['risk_group_id'] = group_id

    try:
        chat = await context.bot.get_chat(int(group_id))
        group_name = chat.title
    except Exception:
        group_name = "the selected group"

    await query.edit_message_text(text=f"You have selected '{group_name}'.\n\nPlease send the media (photo, video, or voice note) you want to risk.")
    return AWAIT_MEDIA

async def receive_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the media, performs the risk based on new logic, and saves the data."""
    user = update.effective_user
    group_id = context.user_data.get('risk_group_id')

    if not group_id:
        await update.message.reply_text("Something went wrong. Your group selection was lost. Please start over with /risk.")
        return ConversationHandler.END

    message = update.message
    media_type = None
    file_id = None

    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.voice:
        media_type = "voice"
        file_id = message.voice.file_id

    if not media_type:
        await update.message.reply_text("That's not a valid media type. Please send a photo, video, or voice note.")
        return AWAIT_MEDIA

    # True = Unlucky = Post the media
    # False = Lucky = Do not post the media (but offer buttons)
    should_post_automatically = random.choice([True, False])
    logger.debug(f"Risk check for user {user.id} in group {group_id}. should_post_automatically={should_post_automatically}")

    risk_data = load_risk_data()
    risk_id = uuid.uuid4().hex

    # The 'risk_failed' flag now correctly represents the user's perspective:
    # True means they were unlucky and got posted. False means they were lucky.
    new_risk = {
        'risk_id': risk_id,
        'user_id': user.id,
        'username': user.username,
        'group_id': group_id,
        'media_type': media_type,
        'file_id': file_id,
        'risk_failed': should_post_automatically,
        'timestamp': int(time.time()),
        'posted_message_id': None
    }

    if should_post_automatically:
        # UNLUCKY CASE: Post automatically
        logger.debug(f"User {user.id} was unlucky. Automatically posting media to group {group_id}.")
        user_mention = user.mention_html()
        caption = f"{user_mention} decided to risk fate and failed miserably! 😈"

        posted_message = None
        try:
            if media_type == 'photo':
                posted_message = await context.bot.send_photo(group_id, file_id, caption=caption, parse_mode='HTML')
            elif media_type == 'video':
                posted_message = await context.bot.send_video(group_id, file_id, caption=caption, parse_mode='HTML')
            elif media_type == 'voice':
                posted_message = await context.bot.send_voice(group_id, file_id, caption=caption, parse_mode='HTML')

            if posted_message:
                new_risk['posted_message_id'] = posted_message.message_id
                await update.message.reply_text("You were unlucky! Your media has been posted.")
            else:
                # This case should be rare, but handle it.
                await update.message.reply_text("You were unlucky... but I failed to post your media. Your secret is safe for now.")

        except Exception as e:
            logger.error(f"Failed to automatically post risk {risk_id} for user {user.id}: {e}")
            await update.message.reply_text("You were unlucky... but I couldn't post your media. Perhaps my permissions in the group have changed.")

        # Save data and end conversation
        risk_data.setdefault(str(user.id), []).append(new_risk)
        save_risk_data(risk_data)
        context.user_data.pop('risk_group_id', None)
        return ConversationHandler.END

    else:
        # LUCKY CASE: Don't post, but show buttons
        logger.debug(f"User {user.id} was lucky. Preparing to send 'beg' buttons.")
        context.user_data['risk_id_to_beg_for'] = risk_id

        # Save the risk data now, even if not posted yet
        risk_data.setdefault(str(user.id), []).append(new_risk)
        save_risk_data(risk_data)

        keyboard = [
            [InlineKeyboardButton("Please post me anyway Sir 🙏", callback_data='beg_post_yes')],
            [InlineKeyboardButton("Thanks Sir", callback_data='beg_post_no')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"You were lucky! Your {media_type} will not be posted... this time.\n"
            "Unless you want to beg me to post it anyway? 😉",
            reply_markup=reply_markup
        )
        return AWAIT_BEGGING

async def beg_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's decision to beg for a failed risk to be posted."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    decision = query.data

    # Clean up the buttons from the original message
    await query.edit_message_reply_markup(reply_markup=None)

    if decision == 'beg_post_no':
        await query.edit_message_text("As you wish. Your secret is safe... for now.")
    elif decision == 'beg_post_yes':
        risk_id_to_post = context.user_data.get('risk_id_to_beg_for')
        if not risk_id_to_post:
            await query.edit_message_text("I seem to have lost the details of your risk. Please start over with /risk.")
            # End of conversation cleanup will happen finally
            return ConversationHandler.END

        risk_data = load_risk_data()
        user_risks = risk_data.get(str(user_id), [])
        target_risk = next((r for r in user_risks if r['risk_id'] == risk_id_to_post), None)

        if not target_risk:
            await query.edit_message_text("An error occurred: I could not find the risk data to post.")
            return ConversationHandler.END

        user_mention = query.from_user.mention_html()
        caption = f"{user_mention} BEGGED me to be posted without mercy 😈"

        try:
            media_type = target_risk['media_type']
            file_id = target_risk['file_id']
            group_id = target_risk['group_id']

            posted_message = None
            if media_type == 'photo':
                posted_message = await context.bot.send_photo(group_id, file_id, caption=caption, parse_mode='HTML')
            elif media_type == 'video':
                posted_message = await context.bot.send_video(group_id, file_id, caption=caption, parse_mode='HTML')
            elif media_type == 'voice':
                posted_message = await context.bot.send_voice(group_id, file_id, caption=caption, parse_mode='HTML')

            if posted_message:
                target_risk['posted_message_id'] = posted_message.message_id
                save_risk_data(risk_data)

            await query.edit_message_text("You begged well enough. Your media has been posted.")

        except Exception as e:
            logger.error(f"Failed to post begged risk {risk_id_to_post} for user {user_id}: {e}")
            await query.edit_message_text("I couldn't post your media. Perhaps my permissions in the group have changed.")

    # Clean up user_data for this conversation
    if 'risk_group_id' in context.user_data:
        del context.user_data['risk_group_id']
    if 'risk_id_to_beg_for' in context.user_data:
        del context.user_data['risk_id_to_beg_for']

    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the current conversation (risk or post)."""
    message_to_send = "Operation cancelled."

    # Check for risk conversation state
    if 'risk_group_id' in context.user_data:
        context.user_data.pop('risk_group_id', None)
        message_to_send = "The risk has been cancelled."

    # Check for post conversation state
    elif 'post_group_id' in context.user_data:
        for key in ['post_group_id', 'post_media_type', 'post_file_id', 'post_caption']:
            context.user_data.pop(key, None)
        message_to_send = "The post creation process has been cancelled."

    await update.message.reply_text(message_to_send)

    # End the conversation
    return ConversationHandler.END

# =============================
# SeeRisk Command
# =============================
@command_handler_wrapper(admin_only=True)
async def seerisk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to see all risks taken by a specific user."""
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /seerisk <user_id or @username>")
        return

    target_arg = context.args[0]
    target_user_id = None
    risk_data = load_risk_data()

    if target_arg.startswith('@'):
        target_username = target_arg[1:].lower()
        # Search for the user ID corresponding to the username
        for user_id_str, risks in risk_data.items():
            if any(r.get('username', '').lower() == target_username for r in risks):
                target_user_id = user_id_str
                break
        if not target_user_id:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No risk data found for username {target_arg}.")
            return
    elif target_arg.isdigit():
        target_user_id = target_arg
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid input. Please provide a valid user ID or a @username.")
        return

    user_risks = risk_data.get(target_user_id)

    if not user_risks:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No risk data found for user ID {target_user_id}.")
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Found {len(user_risks)} risk(s) for user ID {target_user_id}:")

    for risk in user_risks:
        try:
            group_chat = await context.bot.get_chat(int(risk['group_id']))
            group_name = group_chat.title
        except Exception:
            group_name = f"ID {risk['group_id']}"

        from datetime import datetime
        ts = datetime.fromtimestamp(risk['timestamp']).strftime('%Y-%m-%d %H:%M:%S')

        is_purged = risk.get('purged', False)

        # Build the status line
        if is_purged:
            status = "<b>Status: Purged</b>"
        else:
            # Compatibility for old data: check for 'risk_failed' first, then fall back to 'posted'
            risk_failed_flag = risk.get('risk_failed', risk.get('posted'))
            risk_outcome = "Failed" if risk_failed_flag else "Passed"
            post_status = "Posted" if risk.get('posted_message_id') else "Not Posted"
            status = f"Risk: {risk_outcome}, Status: {post_status}"

        caption = (
            f"Risk taken on: {ts}\n"
            f"Target Group: {group_name}\n"
            f"{status}"
        )

        keyboard = []
        # Only show buttons for non-purged risks
        if not is_purged:
            risk_failed_flag = risk.get('risk_failed', risk.get('posted'))
            # Allow posting only if the risk was failed and it's not already posted.
            if risk_failed_flag and not risk.get('posted_message_id'):
                callback_data = f"postrisk_{risk['user_id']}_{risk['risk_id']}"
                keyboard.append([InlineKeyboardButton("Post Now", callback_data=callback_data)])

            # New "Post with Taunt" button for all risked media
            taunt_callback_data = f"posttaunt_{risk['user_id']}_{risk['risk_id']}"
            keyboard.append([InlineKeyboardButton("Post with Taunt", callback_data=taunt_callback_data)])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        media_type = risk['media_type']
        file_id = risk['file_id']

        try:
            if media_type == 'photo':
                await context.bot.send_photo(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
            elif media_type == 'video':
                await context.bot.send_video(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
            elif media_type == 'voice':
                await context.bot.send_voice(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup, parse_mode='HTML')
        except Exception as e:
            await context.bot.send_message(update.effective_chat.id, text=f"Could not retrieve media for a risk from {ts}. It might be too old or deleted. Error: {e}")


async def post_risk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback to post a specific risk to its group."""
    query = update.callback_query
    await query.answer()

    try:
        _, user_id, risk_id = query.data.split('_')
    except ValueError:
        await query.edit_message_text("Error: Invalid callback data.")
        return

    risk_data = load_risk_data()
    user_risks = risk_data.get(user_id, [])

    target_risk = None
    for risk in user_risks:
        if risk['risk_id'] == risk_id:
            target_risk = risk
            break

    if not target_risk:
        await query.edit_message_text("Error: Could not find this risk. It may have been deleted.")
        return

    if target_risk.get('posted_message_id'):
        await query.edit_message_text("This risk has already been posted.")
        return

    try:
        user = await context.bot.get_chat(int(user_id))
        user_mention = user.mention_html()
    except Exception:
        user_mention = f"User {user_id}"

    caption = f"{user_mention} decided to risk fate and failed miserably! 😈"

    try:
        media_type = target_risk['media_type']
        file_id = target_risk['file_id']
        group_id = target_risk['group_id']

        posted_message = None
        if media_type == 'photo':
            posted_message = await context.bot.send_photo(group_id, file_id, caption=caption, parse_mode='HTML')
        elif media_type == 'video':
            posted_message = await context.bot.send_video(group_id, file_id, caption=caption, parse_mode='HTML')
        elif media_type == 'voice':
            posted_message = await context.bot.send_voice(group_id, file_id, caption=caption, parse_mode='HTML')

        # Update the risk data
        if posted_message:
            target_risk['posted_message_id'] = posted_message.message_id
        save_risk_data(risk_data)

        # Update the admin's message
        original_caption = query.message.caption
        new_caption = original_caption.replace("Status: Not Posted", "Status: Posted")
        await query.edit_message_caption(caption=new_caption, reply_markup=None)
        await context.bot.send_message(chat_id=query.message.chat_id, text="Media has been posted to the group.")

    except Exception as e:
        logger.error(f"Admin failed to post risk {risk_id} for user {user_id}: {e}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"Failed to post media: {e}")


async def post_risk_with_taunt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback to post a specific risk to its group with a taunting message."""
    query = update.callback_query
    await query.answer()

    try:
        # Callback format is "posttaunt_{user_id}_{risk_id}"
        _, user_id, risk_id = query.data.split('_')
    except ValueError:
        await query.edit_message_text("Error: Invalid callback data for taunt.")
        return

    risk_data = load_risk_data()
    user_risks = risk_data.get(user_id, [])
    target_risk = next((r for r in user_risks if r['risk_id'] == risk_id), None)

    if not target_risk:
        await query.edit_message_text("Error: Could not find this risk. It may have been deleted.")
        return

    if target_risk.get('purged'):
        await query.edit_message_text("This risk has already been purged and cannot be posted.")
        await query.edit_message_reply_markup(reply_markup=None)
        return

    try:
        user = await context.bot.get_chat(int(user_id))
        user_mention = user.mention_html()
    except Exception:
        user_mention = f"user {user_id}"

    caption = f"I feel mean, so lets see what {user_mention} sent me 😂"

    try:
        media_type = target_risk['media_type']
        file_id = target_risk['file_id']
        group_id = target_risk['group_id']

        posted_message = None
        if media_type == 'photo':
            posted_message = await context.bot.send_photo(group_id, file_id, caption=caption, parse_mode='HTML')
        elif media_type == 'video':
            posted_message = await context.bot.send_video(group_id, file_id, caption=caption, parse_mode='HTML')
        elif media_type == 'voice':
            posted_message = await context.bot.send_voice(group_id, file_id, caption=caption, parse_mode='HTML')

        if posted_message:
            target_risk['posted_message_id'] = posted_message.message_id
            save_risk_data(risk_data)

        await context.bot.send_message(chat_id=query.message.chat_id, text="Media has been posted to the group with a taunt.")

        # Update the original message to remove the buttons
        await query.edit_message_reply_markup(reply_markup=None)

    except Exception as e:
        error_message = f"An unexpected error occurred: {e}"
        # These string checks are a bit fragile, but they are the best we can do without specific exception types
        if "chat not found" in str(e).lower():
            error_message = "Failed to post: The group does not exist anymore."
        elif "bot is not a member" in str(e).lower():
            error_message = "Failed to post: I am no longer in that group."

        logger.error(f"Admin failed to post taunt risk {risk_id} for user {user_id}: {e}")
        await context.bot.send_message(chat_id=query.message.chat_id, text=error_message)


@command_handler_wrapper(admin_only=True)
async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to post a random, non-purged risk from any user."""
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        return

    risk_data = load_risk_data()
    all_risks = [risk for user_risks in risk_data.values() for risk in user_risks]

    # Filter for risks that have media and are not purged
    valid_risks = [
        r for r in all_risks
        if r.get('file_id') and not r.get('purged')
    ]

    if not valid_risks:
        await context.bot.send_message(chat_id=chat.id, text="There are no available risks to choose from.")
        return

    chosen_risk = random.choice(valid_risks)

    user_id = chosen_risk['user_id']
    media_type = chosen_risk['media_type']
    file_id = chosen_risk['file_id']

    try:
        user = await context.bot.get_chat(int(user_id))
        user_mention = user.mention_html()
    except Exception:
        user_mention = f"a mysterious user (ID: {user_id})"

    caption = f"A random risk from {user_mention} has been chosen! Let's see what it is... 👀"

    try:
        if media_type == 'photo':
            await context.bot.send_photo(chat.id, file_id, caption=caption, parse_mode='HTML')
        elif media_type == 'video':
            await context.bot.send_video(chat.id, file_id, caption=caption, parse_mode='HTML')
        elif media_type == 'voice':
            await context.bot.send_voice(chat.id, file_id, caption=caption, parse_mode='HTML')

        logger.info(f"Admin {update.effective_user.id} used /random to post risk {chosen_risk['risk_id']} to group {chat.id}")

    except Exception as e:
        logger.error(f"Failed to post random risk {chosen_risk['risk_id']}: {e}")
        await context.bot.send_message(chat_id=chat.id, text="Sorry, I couldn't post the chosen random risk.")


# =============================
# Purge Command (Big Red Button)
# =============================

async def _do_purge(user_id: int, risks_to_purge: list, context: ContextTypes.DEFAULT_TYPE, admin_user_id: int = None):
    """
    Helper function to perform the actual purge.
    Marks all specified risks as purged and deletes any that were posted.
    Assumes the risks_to_purge list has already been filtered for disabled groups.
    """
    if not risks_to_purge:
        return

    risk_data = load_risk_data()
    user_risks = risk_data.get(str(user_id), [])

    risk_ids_to_purge = {r['risk_id'] for r in risks_to_purge}
    posted_risks_to_delete = [r for r in risks_to_purge if r.get('posted_message_id')]

    success_count = 0
    failure_count = 0

    # First, delete the messages from groups
    for risk in posted_risks_to_delete:
        group_id = risk['group_id']
        message_id = risk['posted_message_id']
        try:
            await context.bot.delete_message(chat_id=int(group_id), message_id=int(message_id))
            logger.info(f"Successfully deleted message {message_id} in group {group_id} for user {user_id}.")
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to delete message {message_id} in group {group_id} for user {user_id}: {e}")
            failure_count += 1

    # Now, update the data file
    for risk in user_risks:
        if risk['risk_id'] in risk_ids_to_purge:
            risk['purged'] = True
            risk['posted_message_id'] = None # Ensure it's cleared

    save_risk_data(risk_data)

    # Determine who to send the confirmation message to
    final_user_id = admin_user_id if admin_user_id else user_id

    summary_message = f"✅ Purge complete for user {user_id}.\n\n"
    summary_message += f"Marked {len(risks_to_purge)} risk(s) as purged.\n"
    if posted_risks_to_delete:
        summary_message += f"Attempted to delete {len(posted_risks_to_delete)} posted message(s):\n"
        summary_message += f"  - Successfully deleted: {success_count}\n"
        summary_message += f"  - Failed to delete: {failure_count}\n"

    if failure_count > 0:
        summary_message += "\n(Failures can happen if a message was already deleted or if I no longer have permission to delete messages in that group.)"

    await context.bot.send_message(chat_id=final_user_id, text=summary_message)


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the /purge conversation. Admins can specify a user to purge."""
    if update.effective_chat.type != "private":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="The /purge command is only available in private chat."
        )
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="Please use the /purge command here to start the process."
            )
        except Exception:
            pass
        return ConversationHandler.END

    user = update.effective_user
    risk_data = load_risk_data()
    disabled_commands = load_disabled_commands()

    # Determine the target user and their info
    target_user_id = None
    target_user_info = ""
    is_admin_purge = bool(context.args)

    if is_admin_purge:
        if not is_admin(user.id):
            await update.message.reply_text("You are not authorized to purge another user's risks.")
            return ConversationHandler.END

        target_arg = context.args[0]
        if target_arg.startswith('@'):
            target_username = target_arg[1:].lower()
            for user_id_str, risks in risk_data.items():
                if any(r.get('username', '').lower() == target_username for r in risks):
                    target_user_id = user_id_str
                    break
            if not target_user_id:
                await update.message.reply_text(f"No risk data found for username {target_arg}.")
                return ConversationHandler.END
            target_user_info = f"user @{target_username}"
        elif target_arg.isdigit():
            target_user_id = target_arg
            try:
                target_user_obj = await context.bot.get_chat(int(target_user_id))
                target_user_info = target_user_obj.mention_html()
            except Exception:
                target_user_info = f"user with ID `{target_user_id}`"
        else:
            await update.message.reply_text("Invalid argument. Please provide a user ID or a @username.")
            return ConversationHandler.END
    else:
        target_user_id = str(user.id)
        target_user_info = "your"


    # --- Universal Filtering Logic ---
    all_user_risks = risk_data.get(str(target_user_id), [])
    non_purged_risks = [r for r in all_user_risks if not r.get('purged')]

    if not non_purged_risks:
        await update.message.reply_text(f"No active risks found for {target_user_info} to purge.", parse_mode='HTML')
        return ConversationHandler.END

    risks_to_purge = []
    skipped_groups_info = set()
    for risk in non_purged_risks:
        group_id = str(risk['group_id'])
        if 'purge' in disabled_commands.get(group_id, []):
            try:
                chat = await context.bot.get_chat(int(group_id))
                skipped_groups_info.add(chat.title)
            except Exception:
                skipped_groups_info.add(f"Group ID {group_id}")
        else:
            risks_to_purge.append(risk)

    if not risks_to_purge:
        await update.message.reply_text(f"All of {target_user_info} risks are in groups where the /purge command is disabled. No action will be taken.", parse_mode='HTML')
        return ConversationHandler.END

    # --- Admin Confirmation Flow ---
    if is_admin_purge:
        context.user_data['purge_target_user_id'] = target_user_id
        context.user_data['risks_to_purge'] = risks_to_purge

        confirmation_message = (
            f"🚨 <b>Admin Purge</b> 🚨\n\n"
            f"You are about to purge {len(risks_to_purge)} risks for {target_user_info}. "
            f"This will delete any posted media and mark all their risked media as purged.\n\n"
            f"This action is irreversible and will bypass any conditions.\n\n"
        )
        if skipped_groups_info:
            confirmation_message += (
                f"Risks in the following groups will be ignored because /purge is disabled:\n"
                f"- {', '.join(sorted(list(skipped_groups_info)))}\n\n"
            )
        confirmation_message += "Are you sure you want to proceed?"
        keyboard = [[InlineKeyboardButton("Yes, purge them.", callback_data='admin_purge_confirm'), InlineKeyboardButton("No, cancel.", callback_data='purge_cancel')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(confirmation_message, reply_markup=reply_markup, parse_mode='HTML')
        return CONFIRM_PURGE

    # --- Regular User Confirmation Flow ---
    conditions_data = load_conditions_data()
    risks_with_conditions = []
    risks_without_conditions = []

    posted_risks = [r for r in risks_to_purge if r.get('posted_message_id')]
    non_posted_risks = [r for r in risks_to_purge if not r.get('posted_message_id')]

    for risk in posted_risks:
        group_id = str(risk['group_id'])
        if isinstance(conditions_data, dict) and group_id in conditions_data and conditions_data[group_id]:
            risks_with_conditions.append(risk)
        else:
            risks_without_conditions.append(risk)

    risks_without_conditions.extend(non_posted_risks)

    context.user_data['risks_with_conditions'] = risks_with_conditions
    context.user_data['risks_without_conditions'] = risks_without_conditions
    context.user_data['purge_target_user_id'] = target_user_id

    confirmation_message = (
        f"🚨 <b>Warning!</b> 🚨\n\n"
        f"You are about to purge <b>{len(risks_to_purge)}</b> of your risks. This action is irreversible.\n"
        f"This includes <b>{len(posted_risks)}</b> posted item(s) and <b>{len(non_posted_risks)}</b> unposted item(s).\n\n"
    )

    if risks_without_conditions:
        confirmation_message += f"• <b>{len(risks_without_conditions)}</b> risks will be purged immediately.\n"
    if risks_with_conditions:
        confirmation_message += f"• <b>{len(risks_with_conditions)}</b> posted risks will require admin verification to be deleted.\n"

    if skipped_groups_info:
        confirmation_message += (
            f"\nRisks in the following groups will be ignored because /purge is disabled:\n"
            f"- {', '.join(sorted(list(skipped_groups_info)))}\n\n"
        )
    confirmation_message += "\nAre you sure you want to proceed?"

    keyboard = [[InlineKeyboardButton("Yes, I'm sure. Purge them.", callback_data='purge_confirm'), InlineKeyboardButton("No, cancel.", callback_data='purge_cancel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(confirmation_message, reply_markup=reply_markup, parse_mode='HTML')

    return CONFIRM_PURGE

async def send_random_condition(user: User, user_data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Selects a random condition from applicable groups, sends it to the user, and notifies admins."""
    risks_to_purge = user_data.get('risks_to_purge', []) # These are now only the risks with conditions
    if not risks_to_purge:
        # This should not happen if called correctly, but as a safeguard:
        await context.bot.send_message(chat_id=user.id, text="No risks requiring a condition were found to purge.")
        return ConversationHandler.END

    # Collect all conditions from the groups where the risks are
    group_ids_with_risks = {risk['group_id'] for risk in risks_to_purge}
    conditions_data = load_conditions_data()
    applicable_conditions = []
    if isinstance(conditions_data, dict):
        for group_id in group_ids_with_risks:
            if group_id in conditions_data:
                applicable_conditions.extend(conditions_data[group_id])

    # If no conditions are found for any of the relevant groups, purge directly.
    if not applicable_conditions:
        await context.bot.send_message(chat_id=user.id, text="No conditions found for the relevant groups. Proceeding with deletion.")
        await _do_purge(user.id, user_data, context)
        # Clean up user_data
        user_data.pop('risks_to_purge', None)
        user_data.pop('risks_with_conditions', None)
        return ConversationHandler.END

    condition = random.choice(applicable_conditions)
    user_data['current_condition'] = condition

    await context.bot.send_message(
        chat_id=user.id,
        text=f"An admin has been sent the following condition to verify:\n\n<b>Condition:</b> {html.escape(condition['text'])}\n\nPlease wait for an admin to confirm that you have met this condition.",
        parse_mode='HTML'
    )

    # The rest of the logic for notifying admins remains the same, as it's already based on the groups from risks_to_purge
    group_ids = {r['group_id'] for r in risks_to_purge}
    admin_ids = set()
    admin_data = load_admin_data()
    for admin_id, groups in admin_data.items():
        if any(g in group_ids for g in groups):
            admin_ids.add(int(admin_id))
    if is_owner(OWNER_ID):
        admin_ids.add(OWNER_ID)

    keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"purge_verify_approve_{user.id}"), InlineKeyboardButton("❌ Deny", callback_data=f"purge_verify_deny_{user.id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    notification_text = (
        f"🚨 <b>Purge Verification Request</b> 🚨\n\n"
        f"User {user.mention_html()} (<code>{user.id}</code>) is requesting to purge their risks.\n\n"
        f"<b>Condition to verify:</b>\n<i>{html.escape(condition['text'])}</i>\n\n"
        f"Please confirm whether the user has met this condition."
    )
    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification_text, reply_markup=reply_markup, parse_mode='HTML')
        except Exception as e:
            logger.warning(f"Failed to send purge verification to admin {admin_id}: {e}")

    return AWAIT_CONDITION_VERIFICATION

async def purge_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's confirmation for purging risks."""
    query = update.callback_query
    await query.answer()
    user = query.from_user # This is the person clicking the button (the admin or the user)

    if query.data == 'purge_cancel':
        await query.edit_message_text("Operation cancelled. Your risks have not been deleted.")
        # Clean up all possible keys
        for key in ['risks_with_conditions', 'risks_without_conditions', 'purge_target_user_id', 'risks_to_purge']:
            context.user_data.pop(key, None)
        return ConversationHandler.END

    await query.edit_message_text("Confirmed. Processing request...")

    target_user_id = context.user_data.get('purge_target_user_id')
    if not target_user_id:
        await query.edit_message_text("Error: Could not find target user for purge. Please start over.")
        return ConversationHandler.END

    # Handle admin purge confirmation
    if query.data == 'admin_purge_confirm':
        risks_to_purge = context.user_data.get('risks_to_purge', [])
        await _do_purge(target_user_id, risks_to_purge, context, admin_user_id=user.id)
        # Clean up
        for key in ['purge_target_user_id', 'risks_to_purge']:
            context.user_data.pop(key, None)
        return ConversationHandler.END

    # Handle regular user purge confirmation
    if query.data == 'purge_confirm':
        risks_with_conditions = context.user_data.get('risks_with_conditions', [])
        risks_without_conditions = context.user_data.get('risks_without_conditions', [])

        # Immediately purge risks from groups without conditions
        if risks_without_conditions:
            await _do_purge(target_user_id, risks_without_conditions, context)
            context.user_data.pop('risks_without_conditions')

        # Now, handle risks from groups that have conditions
        if risks_with_conditions:
            # Set the remaining risks as the ones to be purged for the next step
            context.user_data['risks_to_purge'] = risks_with_conditions
            # We need the target user object for send_random_condition
            target_user_obj = await context.bot.get_chat(target_user_id)
            return await send_random_condition(target_user_obj, context.user_data, context)
        else:
            # If there were no risks with conditions, we're done.
            await context.bot.send_message(chat_id=user.id, text="All applicable risks have been processed.")
            context.user_data.pop('risks_with_conditions', None)
            context.user_data.pop('purge_target_user_id', None)
            return ConversationHandler.END

    # Fallback for unknown callback data
    return ConversationHandler.END


# =============================
# /post Command Conversation
# =============================

async def purge_verification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles an admin's verification of a purge condition."""
    query = update.callback_query
    await query.answer()

    admin_user = query.from_user
    try:
        _, _, decision, user_id_str = query.data.split('_')
        user_id = int(user_id_str)
    except (ValueError, IndexError):
        await query.edit_message_text("Error: Invalid callback data.")
        return

    user_data = context.application.user_data.get(user_id)
    if not user_data or 'risks_to_purge' not in user_data:
        await query.edit_message_text(text="This purge request is no longer valid or has been cancelled by the user.")
        return

    original_message_text = query.message.text

    if decision == 'approve':
        await query.edit_message_text(text=f"{original_message_text}\n\n---\n✅ Approved by {admin_user.mention_html()}", parse_mode='HTML')
        await context.bot.send_message(chat_id=user_id, text="An admin has approved your request. The deletion process will now begin.")

        risks_to_purge = user_data.get('risks_to_purge', [])
        await _do_purge(user_id, risks_to_purge, context, admin_user_id=admin_user.id)

        # Clean up all related data after the final step
        user_data.pop('risks_to_purge', None)
        user_data.pop('current_condition', None)
        user_data.pop('risks_with_conditions', None)
        user_data.pop('purge_target_user_id', None)


    elif decision == 'deny':
        await query.edit_message_text(text=f"{original_message_text}\n\n---\n❌ Denied by {admin_user.mention_html()}", parse_mode='HTML')
        await context.bot.send_message(chat_id=user_id, text="An admin has denied your request. You will now be given a new condition.")

        user_object = await context.bot.get_chat(user_id)
        await send_random_condition(user_object, user_data, context)


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the /post conversation. Asks admin to select a group to post in."""
    user_id = update.effective_user.id
    if update.effective_chat.type != "private":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="The /post command is only available in private chat."
        )
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="Please use the /post command here to start creating a post."
            )
        except Exception:
            pass # Ignore if user has not started a chat with the bot
        return ConversationHandler.END

    if not is_admin(user_id):
        await update.message.reply_text("This is an admin-only command. You are not authorized.")
        return ConversationHandler.END

    admin_data = load_admin_data()
    # In Python 3, .get() on a dictionary with a default value is safe.
    # The user_id needs to be a string for JSON key matching.
    user_admin_groups = admin_data.get(str(user_id), [])

    if not user_admin_groups:
        await update.message.reply_text("You are not registered as an admin in any groups that I'm aware of. Try running /update in a group where you are an admin.")
        return ConversationHandler.END

    disabled_data = load_disabled_commands()
    keyboard = []
    for group_id in user_admin_groups:
        # Check if 'post' command is disabled for this group
        if 'post' in disabled_data.get(str(group_id), []):
            continue  # Skip this group

        try:
            chat = await context.bot.get_chat(int(group_id))
            keyboard.append([InlineKeyboardButton(chat.title, callback_data=f"post_group_{group_id}")])
        except Exception as e:
            logger.warning(f"Could not fetch chat info for group {group_id} for /post command: {e}")

    if not keyboard:
        await update.message.reply_text("There are no available groups for you to post in. The /post command may be disabled in the groups where you are an admin.")
        return ConversationHandler.END

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Please choose a group to post your message in:", reply_markup=reply_markup)
    return SELECT_POST_GROUP

async def select_post_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles group selection and asks for media."""
    query = update.callback_query
    await query.answer()

    group_id = query.data.replace("post_group_", "")
    context.user_data['post_group_id'] = group_id

    try:
        chat = await context.bot.get_chat(int(group_id))
        group_name = chat.title
    except Exception:
        group_name = "the selected group"

    await query.edit_message_text(text=f"You have selected '{group_name}'.\n\nPlease send the media (photo or video) for your post.")
    return AWAIT_POST_MEDIA

async def receive_post_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving the media for the post."""
    message = update.message
    media_type = None
    file_id = None

    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    else:
        await update.message.reply_text("This is not a valid media type. Please send a photo or a video.")
        return AWAIT_POST_MEDIA # Remain in the same state

    context.user_data['post_media_type'] = media_type
    context.user_data['post_file_id'] = file_id

    await update.message.reply_text("Media received. Now, please enter the caption for your post.")
    return AWAIT_POST_CAPTION

async def receive_post_caption_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving the caption and shows a preview."""
    caption = update.message.text
    if not caption:
        await update.message.reply_text("Please provide a caption for your post.")
        return AWAIT_POST_CAPTION

    context.user_data['post_caption'] = caption
    media_type = context.user_data['post_media_type']
    file_id = context.user_data['post_file_id']

    # Show preview
    await update.message.reply_text("Here is a preview of your post:")

    keyboard = [
        [
            InlineKeyboardButton("Confirm & Post", callback_data='post_confirm'),
            InlineKeyboardButton("Cancel", callback_data='post_cancel')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if media_type == 'photo':
            await context.bot.send_photo(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup)
        elif media_type == 'video':
            await context.bot.send_video(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error sending preview for /post command: {e}")
        await update.message.reply_text("There was an error showing the preview. Please try again.")
        return AWAIT_POST_MEDIA

    return CONFIRM_POST

async def post_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the final confirmation from the admin."""
    query = update.callback_query
    await query.answer()

    # It's good practice to remove the buttons from the preview message to prevent double-clicks
    await query.edit_message_reply_markup(reply_markup=None)

    if query.data == 'post_confirm':
        group_id = context.user_data.get('post_group_id')
        media_type = context.user_data.get('post_media_type')
        file_id = context.user_data.get('post_file_id')
        caption = context.user_data.get('post_caption')

        if not all([group_id, media_type, file_id, caption]):
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="An error occurred, some information was lost. Please start over with /post."
            )
            # Clean up potentially partial data
            for key in ['post_group_id', 'post_media_type', 'post_file_id', 'post_caption']:
                context.user_data.pop(key, None)
            return ConversationHandler.END

        try:
            if media_type == 'photo':
                await context.bot.send_photo(group_id, file_id, caption=caption)
            elif media_type == 'video':
                await context.bot.send_video(group_id, file_id, caption=caption)

            # Send a new message as confirmation
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="✅ Your post has been sent successfully!"
            )
        except Exception as e:
            logger.error(f"Failed to send post to group {group_id}: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"An error occurred while trying to post. I might not have the right permissions in the target group.\nError: {e}"
            )

    elif query.data == 'post_cancel':
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Post cancelled."
        )

    # Clean up user_data
    for key in ['post_group_id', 'post_media_type', 'post_file_id', 'post_caption']:
        context.user_data.pop(key, None)

    return ConversationHandler.END

# =============================
# /command - List all commands
# =============================
COMMAND_MAP = {
    'start': {'is_admin': False}, 'help': {'is_admin': False}, 'beowned': {'is_admin': False},
    'command': {'is_admin': False}, 'disable': {'is_admin': True}, 'admin': {'is_admin': False},
    'link': {'is_admin': True}, 'inactive': {'is_admin': True}, 'post': {'is_admin': True},
    'setnickname': {'is_admin': True}, 'removenickname': {'is_admin': True}, 'allban': {'is_admin': True},
    'enable': {'is_admin': True}, 'update': {'is_admin': True}, 'risk': {'is_admin': False},
    'seerisk': {'is_admin': True}, 'purge': {'is_admin': False}, 'random': {'is_admin': True},
    'addcondition': {'is_admin': True}, 'listconditions': {'is_admin': True}, 'removecondition': {'is_admin': True},
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
DISABLED_COMMANDS_FILE = BASE_DIR / 'disabled_commands.json'

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
    """Handles the /start command with different messages for private and group chats."""
    if context.args and context.args[0].startswith('setstake_'):
        return  # This is handled by a different conversation handler

    user = update.effective_user
    chat = update.effective_chat

    # Update user activity in groups
    if user and chat and chat.type in ["group", "supergroup"]:
        update_user_activity(user.id, chat.id)

    # Define the detailed private start message
    private_start_message = """
Hello! I'm a bot designed to help manage groups and add a bit of fun. Here are the main commands to get you started:

- /help: Shows a full menu of all my available commands.
- /command: When used in a group, this lists all commands available in that specific group.
- /risk: Feeling lucky? Use this command in our private chat to risk posting some media to a group.

If you encounter any bugs or have ideas for new features, please contact my creator: @BeansOfBeano
"""

    if chat.type == "private":
        # In a private chat, send the detailed message
        await context.bot.send_message(
            chat_id=chat.id,
            text=private_start_message,
            disable_web_page_preview=True
        )
    else:
        # In a group chat, send a prompt and try to message the user privately
        group_start_message = f"Hey {user.mention_html()}! Please message me in private to get started."
        await context.bot.send_message(chat_id=chat.id, text=group_start_message, parse_mode='HTML')
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=private_start_message,
                disable_web_page_preview=True
            )
        except Exception:
            logger.warning(f"Failed to send private start message to {user.id} who started in group {chat.id}")

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
- /risk: Take a risk and let fate decide if your media gets posted. (Private chat only)
- /purge: Marks all your risked media as purged, deleting any that were posted, subject to group conditions. (Private chat only)
- /cancel: Cancels an ongoing operation like /risk or /post.
        """
    elif topic == 'help_admin':
        if not is_admin(user_id):
            await query.answer("You are not authorized to view this section.", show_alert=True)
            return

        text = """
<b>Administrator Commands</b>

<u>Content & User Management</u>
- /allban &lt;user&gt;: Bans a user from all groups the bot is in.
- /post: Create a post with media and a caption to send to a group where you are an admin. (Private chat only)
- /disable &lt;command&gt;: Disables a static command or a dynamic hashtag command in the current group.
- /enable &lt;command&gt;: Re-enables a disabled static command.
- /link: Generates a single-use invite link for the group.
- /inactive &lt;days&gt;: Sets up automatic kicking for users who are inactive for a specified number of days (e.g., /inactive 30). Use 0 to disable.

<u>Admin & User Identity</u>
- /update: Refreshes the bot's list of admins for the current group. Run this when admin roles change.
- /setnickname &lt;user&gt; &lt;nickname&gt;: Sets a custom nickname for a user. You can reply to a user or use their ID.
- /removenickname &lt;user&gt;: Removes a user's nickname.

<u>Risk & History</u>
- /seerisk &lt;user_id or @username&gt;: View the risk history of a specific user.
- /purge &lt;user_id or @username&gt;: Purges all risks for a specific user, bypassing any conditions.
- /random: Posts a random, non-purged risk from any user to the current group.

<u>Purge Conditions (Owner-only)</u>
- /addcondition &lt;condition&gt;: Adds a condition that users must meet to use /purge.
- /listconditions: Lists all current purge conditions with their IDs.
- /removecondition &lt;id&gt;: Removes a purge condition by its ID.
"""
        # Append dynamic hashtag commands if they exist
        hashtag_data = load_hashtag_data()
        if hashtag_data:
            text += "\n<b>Dynamic Hashtag Commands (Admin-only):</b>\n"
            text += '\n'.join(f"/{tag}" for tag in sorted(hashtag_data.keys()))
            text += "\n<i>These are created by posting with a hashtag and can be removed with /disable.</i>"

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
    # Conversation handler for the /risk command
    risk_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('risk', risk_command)],
        states={
            SELECT_GROUP: [CallbackQueryHandler(select_group_callback, pattern='^risk_group_')],
            AWAIT_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.VOICE, receive_media_handler)],
            AWAIT_BEGGING: [CallbackQueryHandler(beg_callback_handler, pattern='^beg_post_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False,
        per_user=True
    )
    app.add_handler(risk_conv_handler)

    # Conversation handler for the /post command
    post_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('post', post_command)],
        states={
            SELECT_POST_GROUP: [CallbackQueryHandler(select_post_group_callback, pattern='^post_group_')],
            AWAIT_POST_MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, receive_post_media_handler)],
            AWAIT_POST_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_post_caption_handler)],
            CONFIRM_POST: [CallbackQueryHandler(post_confirmation_callback, pattern='^post_confirm$|^post_cancel$')]
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False,
        per_user=True
    )
    app.add_handler(post_conv_handler)

    # Conversation handler for the /purge command
    purge_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('purge', purge_command)],
        states={
            CONFIRM_PURGE: [CallbackQueryHandler(purge_confirmation_callback, pattern=r'^(purge_confirm|purge_cancel|admin_purge_confirm)$')],
            AWAIT_CONDITION_VERIFICATION: [], # User waits in this state for admin action
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_message=False,
        per_user=True
    )
    app.add_handler(purge_conv_handler)

    # Register all commands using the new helper
    add_command(app, 'cancel', cancel_command)
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
    add_command(app, 'allban', allban_command)
    add_command(app, 'addcondition', addcondition_command)
    add_command(app, 'listconditions', listconditions_command)
    add_command(app, 'removecondition', removecondition_command)
    add_command(app, 'enable', enable_command)
    add_command(app, 'update', update_command)
    add_command(app, 'seerisk', seerisk_command)
    add_command(app, 'random', random_command)
    add_command(app, 'risk', risk_command)
    add_command(app, 'post', post_command)
    add_command(app, 'purge', purge_command)

    app.add_handler(CallbackQueryHandler(help_menu_handler, pattern=r'^help_'))
    app.add_handler(CallbackQueryHandler(post_risk_callback, pattern=r'^postrisk_'))
    app.add_handler(CallbackQueryHandler(post_risk_with_taunt_callback, pattern=r'^posttaunt_'))
    app.add_handler(CallbackQueryHandler(purge_verification_callback, pattern=r'^purge_verify_'))

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
