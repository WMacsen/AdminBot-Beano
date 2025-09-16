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
from telegram import Update, User, InlineKeyboardButton, InlineKeyboardMarkup, Message
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
    "timer": asyncio.Lock(),
    "no_delete": asyncio.Lock(),
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
TIMER_SETTINGS_FILE = BASE_DIR / 'timer_settings.json'
NO_DELETE_IDS_FILE = BASE_DIR / 'no_delete_ids.json'
OWNER_ID = 7237569475  # Your Telegram ID (change to your actual Telegram user ID)

def load_timer_settings():
    if os.path.exists(TIMER_SETTINGS_FILE):
        with open(TIMER_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_timer_settings(data):
    with open(TIMER_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_no_delete_ids():
    if os.path.exists(NO_DELETE_IDS_FILE):
        with open(NO_DELETE_IDS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_no_delete_ids(data):
    with open(NO_DELETE_IDS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
                        sent_message = await context.bot.send_message(
                            chat_id=chat.id,
                            text=f"Warning: {user.mention_html()}, you are not authorized to use this command.",
                            parse_mode='HTML'
                        )
                        await schedule_message_deletion(context, sent_message)
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
RANDOM_RISK_SETTINGS_FILE = BASE_DIR / 'random_risk_settings.json'

def load_random_risk_settings():
    if os.path.exists(RANDOM_RISK_SETTINGS_FILE):
        with open(RANDOM_RISK_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_random_risk_settings(data):
    with open(RANDOM_RISK_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_risk_data():
    if os.path.exists(RISK_DATA_FILE):
        try:
            with open(RISK_DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            # Corrupted file detected
            corrupted_file_path = RISK_DATA_FILE.with_suffix('.json.corrupted')
            try:
                os.rename(RISK_DATA_FILE, corrupted_file_path)
                logger.error(f"Risk data file was corrupted. Moved to {corrupted_file_path}. Starting with empty risk data.")
            except OSError as e:
                logger.error(f"Could not rename corrupted risk data file: {e}")
            return {}
    return {}

def save_risk_data(data):
    # Create a temporary file path
    temp_file_path = RISK_DATA_FILE.with_suffix('.json.tmp')
    try:
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Atomically replace the original file with the temporary file
        os.replace(temp_file_path, RISK_DATA_FILE)
    except (OSError, IOError) as e:
        logger.error(f"Could not save risk data to {RISK_DATA_FILE}: {e}")

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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Only the owner can use this command.")
        await schedule_message_deletion(context, sent_message)
        return

    target_id = None
    nickname = ""

    reply_message = update.message.reply_to_message
    if reply_message:
        if not context.args:
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: Reply to a message with `/setnickname <nickname>`")
            await schedule_message_deletion(context, sent_message)
            return
        target_id = reply_message.from_user.id
        nickname = " ".join(context.args)
    else:
        if len(context.args) < 2 or not context.args[0].isdigit():
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: `/setnickname <user_id> <nickname>` or reply to a user's message.")
            await schedule_message_deletion(context, sent_message)
            return

        target_id = int(context.args[0])
        nickname = " ".join(context.args[1:])

    if not target_id:
        # This case is unlikely to be reached now but serves as a safeguard.
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Could not identify the target user.")
        await schedule_message_deletion(context, sent_message)
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

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Nickname for {target_user_info} has been set to '{nickname}'.", parse_mode='HTML')
    await schedule_message_deletion(context, sent_message)

@command_handler_wrapper(admin_only=True)
async def removenickname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Only the owner can use this command.")
        await schedule_message_deletion(context, sent_message)
        return

    target_id = None

    reply_message = update.message.reply_to_message
    if reply_message:
        target_id = reply_message.from_user.id
    else:
        if not context.args or not context.args[0].isdigit():
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: Reply to a user with /removenickname, or use `/removenickname <user_id>`.")
            await schedule_message_deletion(context, sent_message)
            return
        target_id = int(context.args[0])

    if not target_id:
        # This case is unlikely to be reached now but serves as a safeguard.
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Could not identify the target user.")
        await schedule_message_deletion(context, sent_message)
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

        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Nickname for {target_user_info} has been removed.", parse_mode='HTML')
        await schedule_message_deletion(context, sent_message)
    else:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This user does not have a nickname set.")
        await schedule_message_deletion(context, sent_message)


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
                sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Could not find a user ID for {arg}. This can happen if I haven't interacted with them before. Please use their user ID or reply to one of their messages.")
                await schedule_message_deletion(context, sent_message)
                return
        else:
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid argument. Please provide a user ID, a @username, or reply to a user's message.")
            await schedule_message_deletion(context, sent_message)
            return
    else:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /allban <user_id or @username> or reply to a user's message.")
        await schedule_message_deletion(context, sent_message)
        return

    if not target_user_id:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Could not identify the target user.")
        await schedule_message_deletion(context, sent_message)
        return

    if is_owner(target_user_id):
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You cannot ban the owner.")
        await schedule_message_deletion(context, sent_message)
        return
    if target_user_id == update.effective_user.id:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You cannot ban yourself.")
        await schedule_message_deletion(context, sent_message)
        return

    admin_data = load_admin_data()
    all_group_ids = {group for groups in admin_data.values() for group in groups}
    disabled_cmds = load_disabled_commands()

    successful_bans = []
    failed_bans = []

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Processing all-ban for {target_user_info}. This may take a moment...", parse_mode='HTML')
    await schedule_message_deletion(context, sent_message)

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

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=summary_message, parse_mode='HTML')
    await schedule_message_deletion(context, sent_message)


@command_handler_wrapper(admin_only=True)
async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sets the percentage chance for a random risk to be posted automatically every 30 minutes.
    """
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        await schedule_message_deletion(context, sent_message)
        return

    if not context.args:
        settings = load_random_risk_settings()
        group_id_str = str(chat.id)
        current_percentage = settings.get(group_id_str, 0)
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"The current random risk chance is {current_percentage}%. Use `/random <percentage>` to change it.")
        await schedule_message_deletion(context, sent_message)
        return

    try:
        percentage = float(context.args[0])
    except ValueError:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Invalid percentage. Please provide a number.")
        await schedule_message_deletion(context, sent_message)
        return

    if not (0 <= percentage <= 100):
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Percentage must be between 0 and 100.")
        await schedule_message_deletion(context, sent_message)
        return

    settings = load_random_risk_settings()
    group_id_str = str(chat.id)

    if percentage == 0:
        if group_id_str in settings:
            del settings[group_id_str]
            save_random_risk_settings(settings)
            sent_message = await context.bot.send_message(chat_id=chat.id, text="Automatic random risk posting has been disabled for this group.")
            await schedule_message_deletion(context, sent_message)
        else:
            sent_message = await context.bot.send_message(chat_id=chat.id, text="Automatic random risk posting is already disabled for this group.")
            await schedule_message_deletion(context, sent_message)
    else:
        settings[group_id_str] = percentage
        save_random_risk_settings(settings)
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"Automatic random risk chance has been set to {percentage}% for this group. Checks will occur every 30 minutes.")
        await schedule_message_deletion(context, sent_message)


@command_handler_wrapper(admin_only=True)
async def addcondition_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        await schedule_message_deletion(context, sent_message)
        return

    if not context.args:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Usage: /addcondition <text of the condition>")
        await schedule_message_deletion(context, sent_message)
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

    sent_message = await context.bot.send_message(chat_id=chat.id, text=f"✅ Condition added with ID: `{new_condition['id']}` for this group.", parse_mode='HTML')
    await schedule_message_deletion(context, sent_message)

@command_handler_wrapper(admin_only=True)
async def listconditions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        await schedule_message_deletion(context, sent_message)
        return

    group_id = str(chat.id)
    conditions_data = load_conditions_data()

    if not isinstance(conditions_data, dict):
        # Handle case where data might be in the old list format or non-existent
        sent_message = await context.bot.send_message(chat_id=chat.id, text="No conditions have been set for this group.")
        await schedule_message_deletion(context, sent_message)
        return

    group_conditions = conditions_data.get(group_id, [])

    if not group_conditions:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="No conditions have been set for this group.")
        await schedule_message_deletion(context, sent_message)
        return

    message = "📜 <b>Current Conditions for this Group</b>\n\n"
    for cond in group_conditions:
        message += f"- <b>ID: {cond['id']}</b>\n  <i>{html.escape(cond['text'])}</i>\n\n"

    sent_message = await context.bot.send_message(chat_id=chat.id, text=message, parse_mode='HTML')
    await schedule_message_deletion(context, sent_message)


@command_handler_wrapper(admin_only=True)
async def removecondition_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        await schedule_message_deletion(context, sent_message)
        return

    if not context.args:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Usage: /removecondition <condition_id>")
        await schedule_message_deletion(context, sent_message)
        return

    condition_id_to_remove = context.args[0]
    group_id = str(chat.id)
    conditions_data = load_conditions_data()

    if not isinstance(conditions_data, dict):
        # Data is not in the expected format, so there's nothing to remove.
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"❌ Could not find a condition with ID `{condition_id_to_remove}` in this group.", parse_mode='HTML')
        await schedule_message_deletion(context, sent_message)
        return

    group_conditions = conditions_data.get(group_id, [])
    if not group_conditions:
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"❌ Could not find a condition with ID `{condition_id_to_remove}` in this group.", parse_mode='HTML')
        await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"✅ Condition with ID `{condition_id_to_remove}` has been removed from this group.", parse_mode='HTML')
        await schedule_message_deletion(context, sent_message)
    else:
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"❌ Could not find a condition with ID `{condition_id_to_remove}` in this group.", parse_mode='HTML')
        await schedule_message_deletion(context, sent_message)


@command_handler_wrapper(admin_only=True)
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /update (admin only): Scans the group for admins and updates the global admin list.
    """
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in a group chat.")
        await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Error: Could not retrieve the list of administrators for this group.")
        await schedule_message_deletion(context, sent_message)
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
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=message)
    await schedule_message_deletion(context, sent_message)


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


async def _notify_admins_of_failed_post(context: ContextTypes.DEFAULT_TYPE, group_id: str, failed_user_id: int, reason: str):
    """Notifies admins of a specific group that an automatic post has failed."""
    logger.info(f"Notifying admins of group {group_id} about a failed post for user {failed_user_id}.")

    admin_data = load_admin_data()
    admin_ids = set()
    for admin_id, groups in admin_data.items():
        if group_id in groups:
            admin_ids.add(int(admin_id))

    # Also notify the owner
    if is_owner(OWNER_ID):
        admin_ids.add(OWNER_ID)

    if not admin_ids:
        logger.warning(f"Could not find any admins for group {group_id} to notify about failed post.")
        return

    try:
        failed_user = await context.bot.get_chat(failed_user_id)
        failed_user_mention = failed_user.mention_html()
    except Exception:
        failed_user_mention = f"user with ID <code>{failed_user_id}</code>"

    try:
        group_chat = await context.bot.get_chat(int(group_id))
        group_name = group_chat.title
    except Exception:
        group_name = f"group with ID <code>{group_id}</code>"

    notification_text = (
        f"🔔 <b>Automatic Post Failure</b> 🔔\n\n"
        f"An automatic risk post for {failed_user_mention} in <b>{group_name}</b> failed.\n\n"
        f"<b>Reason:</b> {html.escape(reason)}\n\n"
        f"You may need to manually post their risk via <code>/seerisk</code> or check my permissions in the group."
    )

    for admin_id in admin_ids:
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification_text, parse_mode='HTML')
        except Exception as e:
            logger.warning(f"Failed to send post failure notification to admin {admin_id}: {e}")


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
            sent_message = await context.bot.send_message(chat_id=admin.user.id, text=notification_text, parse_mode='HTML')
            await schedule_message_deletion(context, sent_message)
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
            sent_message = await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption=entry.get('caption') or entry.get('text') or '')
            await schedule_message_deletion(context, sent_message)
            found = True
        # Send all videos
        for video_id in entry.get('videos', []):
            sent_message = await context.bot.send_video(chat_id=update.effective_chat.id, video=video_id, caption=entry.get('caption') or entry.get('text') or '')
            await schedule_message_deletion(context, sent_message)
            found = True
        # Fallback for text/caption only
        if not entry.get('photos') and not entry.get('videos') and (entry.get('text') or entry.get('caption')):
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=entry.get('text') or entry.get('caption'))
            await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="The /risk command is only available in private chat."
        )
        await schedule_message_deletion(context, sent_message)
        # Attempt to start a private message instead
        try:
            sent_message = await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="Please use the /risk command here."
            )
            await schedule_message_deletion(context, sent_message)
        except Exception:
            pass # Ignore if user has not started a chat with the bot
        return ConversationHandler.END

    admin_data = load_admin_data()
    if not admin_data:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="The bot is not yet configured in any groups. Please use /update in a group first.")
        await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="There are no groups available for the /risk command right now.")
        await schedule_message_deletion(context, sent_message)
        return ConversationHandler.END

    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Choose a group where you want to risk your fate:", reply_markup=reply_markup)
    await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Something went wrong. Your group selection was lost. Please start over with /risk.")
        await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="That's not a valid media type. Please send a photo, video, or voice note.")
        await schedule_message_deletion(context, sent_message)
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
        'posted_message_id': None,
        'purged': False
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
                await schedule_message_deletion(context, posted_message)
                sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You were unlucky! Your media has been posted.")
                await schedule_message_deletion(context, sent_message)
            else:
                # This case should be rare, but handle it.
                sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You were unlucky... but I failed to post your media. Your secret is safe for now.")
                await schedule_message_deletion(context, sent_message)

        except Exception as e:
            logger.error(f"Failed to automatically post risk {risk_id} for user {user.id}: {e}")
            await _notify_admins_of_failed_post(context, group_id, user.id, str(e))
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You were unlucky... but I couldn't post your media. Perhaps my permissions in the group have changed.")
            await schedule_message_deletion(context, sent_message)

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

        sent_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"You were lucky! Your {media_type} will not be posted... this time.\n"
                 "Unless you want to beg me to post it anyway? 😉",
            reply_markup=reply_markup
        )
        await schedule_message_deletion(context, sent_message)
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
                await schedule_message_deletion(context, posted_message)

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

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=message_to_send)
    await schedule_message_deletion(context, sent_message)

    # End the conversation
    return ConversationHandler.END

# =============================
# SeeRisk Command
# =============================
@command_handler_wrapper()
async def seerisk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to see all risks taken by a specific user."""
    if not is_admin(update.effective_user.id):
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You are not authorized to use this command.")
        await schedule_message_deletion(context, sent_message)
        return

    if not context.args:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /seerisk <user_id or @username>")
        await schedule_message_deletion(context, sent_message)
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
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No risk data found for username {target_arg}.")
            await schedule_message_deletion(context, sent_message)
            return
    elif target_arg.isdigit():
        target_user_id = target_arg
    else:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Invalid input. Please provide a valid user ID or a @username.")
        await schedule_message_deletion(context, sent_message)
        return

    user_risks = risk_data.get(target_user_id)

    if not user_risks:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No risk data found for user ID {target_user_id}.")
        await schedule_message_deletion(context, sent_message)
        return

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Found {len(user_risks)} risk(s) for user ID {target_user_id}:")
    await schedule_message_deletion(context, sent_message)

    for risk in user_risks:
        try:
            group_chat = await context.bot.get_chat(int(risk['group_id']))
            group_name = group_chat.title
        except Exception:
            group_name = f"ID {risk['group_id']}"

        from datetime import datetime
        ts = datetime.fromtimestamp(risk['timestamp']).strftime('%Y-%m-%d %H:%M:%S')

        # Compatibility for old data: check for 'risk_failed' first, then fall back to 'posted'
        risk_failed_flag = risk.get('risk_failed', risk.get('posted'))

        risk_outcome = "Failed" if risk_failed_flag else "Passed"
        post_status = "Posted" if risk.get('posted_message_id') else "Not Posted"

        status = f"Risk: {risk_outcome}, Status: {post_status}"
        if risk.get('purged', False):
            status += " [PURGED]"

        caption = (
            f"Risk taken on: {ts}\n"
            f"Target Group: {group_name}\n"
            f"Status: {status}"
        )

        keyboard = []
        # Allow posting only if the risk was failed and it's not already posted.
        if risk_failed_flag and not risk.get('posted_message_id') and not risk.get('purged', False):
            callback_data = f"postrisk_{risk['user_id']}_{risk['risk_id']}"
            keyboard.append([InlineKeyboardButton("Post Now", callback_data=callback_data)])

        # Add "Post with Taunt" button only if the risk is not purged.
        if not risk.get('purged', False):
            taunt_callback_data = f"posttaunt_{risk['user_id']}_{risk['risk_id']}"
            keyboard.append([InlineKeyboardButton("Post with Taunt", callback_data=taunt_callback_data)])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        media_type = risk['media_type']
        file_id = risk['file_id']

        try:
            sent_message = None
            if media_type == 'photo':
                sent_message = await context.bot.send_photo(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup)
            elif media_type == 'video':
                sent_message = await context.bot.send_video(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup)
            elif media_type == 'voice':
                sent_message = await context.bot.send_voice(update.effective_chat.id, file_id, caption=caption, reply_markup=reply_markup)

            # If a message was sent and it had buttons, record it for later editing.
            if sent_message and reply_markup:
                if 'seerisk_messages' not in risk:
                    risk['seerisk_messages'] = []
                risk['seerisk_messages'].append({
                    'chat_id': sent_message.chat.id,
                    'message_id': sent_message.message_id
                })

            if sent_message:
                await schedule_message_deletion(context, sent_message)

        except Exception as e:
            error_message = await context.bot.send_message(update.effective_chat.id, text=f"Could not retrieve media for a risk from {ts}. It might be too old or deleted. Error: {e}")
            await schedule_message_deletion(context, error_message)

    save_risk_data(risk_data)


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
        sent_message = await context.bot.send_message(chat_id=query.message.chat_id, text="Media has been posted to the group.")
        await schedule_message_deletion(context, sent_message)

    except Exception as e:
        logger.error(f"Admin failed to post risk {risk_id} for user {user_id}: {e}")
        sent_message = await context.bot.send_message(chat_id=query.message.chat_id, text=f"Failed to post media: {e}")
        await schedule_message_deletion(context, sent_message)


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

        sent_message = await context.bot.send_message(chat_id=query.message.chat_id, text="Media has been posted to the group with a taunt.")
        await schedule_message_deletion(context, sent_message)

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
        sent_message = await context.bot.send_message(chat_id=query.message.chat_id, text=error_message)
        await schedule_message_deletion(context, sent_message)


# =============================
# Purge Command (Big Red Button)
# =============================

async def _delete_and_mark_risks(risks_to_process: list, context: ContextTypes.DEFAULT_TYPE) -> tuple[int, int]:
    """
    Deletes posted messages and marks risks as purged in the database.
    Returns counts of successful and failed deletions.
    """
    success_count = 0
    failure_count = 0

    if not risks_to_process:
        return 0, 0

    # It's possible for multiple risks to point to the same user, so we get the user ID from the first one.
    user_id = risks_to_process[0]['user_id']
    risk_data = load_risk_data()
    user_risks = risk_data.get(str(user_id), [])

    for risk_to_purge in risks_to_process:
        # Find the definitive risk object from the loaded data
        risk_in_db = next((r for r in user_risks if r['risk_id'] == risk_to_purge['risk_id']), None)
        if not risk_in_db:
            failure_count += 1
            continue

        # Remove taunt buttons from any admin's /seerisk view
        if risk_in_db.get('seerisk_messages'):
            for msg_info in risk_in_db['seerisk_messages']:
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=msg_info['chat_id'],
                        message_id=msg_info['message_id'],
                        reply_markup=None
                    )
                except Exception as e:
                    logger.warning(f"Failed to remove taunt button from seerisk message {msg_info['message_id']}: {e}")

        # Delete the message from the group if it was posted
        if risk_in_db.get('posted_message_id'):
            try:
                await context.bot.delete_message(
                    chat_id=int(risk_in_db['group_id']),
                    message_id=int(risk_in_db['posted_message_id'])
                )
                logger.info(f"Successfully purged message {risk_in_db['posted_message_id']} in group {risk_in_db['group_id']}.")
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to delete message for risk {risk_in_db['risk_id']}: {e}")
                failure_count += 1

        # Mark as purged, clear the posted_message_id, and clear the seerisk_messages
        risk_in_db['purged'] = True
        risk_in_db['posted_message_id'] = None
        risk_in_db.pop('seerisk_messages', None)

    save_risk_data(risk_data)
    return success_count, failure_count


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Starts the /purge conversation for a user on their own risks,
    or allows an admin to purge another user's risks directly.
    """
    user = update.effective_user
    chat = update.effective_chat

    if chat.type != "private":
        sent_message = await context.bot.send_message(
            chat_id=chat.id,
            text="The /purge command is only available in private chat."
        )
        await schedule_message_deletion(context, sent_message)
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text="Please use the /purge command here to start the process."
            )
        except Exception:
            pass # User might have blocked the bot
        return ConversationHandler.END

    # Admin Purge Logic
    if context.args and is_admin(user.id):
        target_arg = context.args[0]
        risk_data = load_risk_data()
        target_user_id = None
        target_user_info = f"'{target_arg}'"

        if target_arg.isdigit():
            target_user_id = target_arg
        elif target_arg.startswith('@'):
            username_to_find = target_arg[1:].lower()
            for user_id_str, risks in risk_data.items():
                if any(r.get('username', '').lower() == username_to_find for r in risks):
                    target_user_id = user_id_str
                    break

        if not target_user_id:
            await update.message.reply_text(f"Could not find a user with risks matching {target_user_info}.")
            return ConversationHandler.END

        user_risks = risk_data.get(target_user_id, [])
        disabled_commands = load_disabled_commands()

        # Admin purge considers all risks, not just those with a posted_message_id
        risks_to_process = []
        for risk in user_risks:
            if 'purge' not in disabled_commands.get(str(risk['group_id']), []):
                risks_to_process.append(risk)

        if not risks_to_process:
            await update.message.reply_text(f"User {target_user_id} has no risks that can be purged (they may all be in groups where /purge is disabled).")
            return ConversationHandler.END

        success_count, failure_count = await _delete_and_mark_risks(risks_to_process, context)

        response_message = (
            f"Purge complete for user {target_user_id}.\n"
            f"✅ Successfully deleted {success_count} posts and marked as purged.\n"
            f"❌ Failed to delete {failure_count} posts (but they were still marked as purged)."
        )
        await update.message.reply_text(response_message)
        return ConversationHandler.END

    # Regular User Purge Logic
    risk_data = load_risk_data()
    user_risks = risk_data.get(str(user.id), [])

    # A user can only purge their own media that has been posted.
    risks_to_purge = [r for r in user_risks if r.get('posted_message_id') and not r.get('purged', False)]

    if not risks_to_purge:
        await update.message.reply_text("You have no active, posted risks to purge.")
        return ConversationHandler.END

    disabled_commands = load_disabled_commands()
    conditions_data = load_conditions_data()

    risks_with_conditions = []
    risks_without_conditions = []
    disabled_groups_info = set()

    for risk in risks_to_purge:
        group_id = risk['group_id']
        if 'purge' in disabled_commands.get(str(group_id), []):
            try:
                chat_info = await context.bot.get_chat(int(group_id))
                disabled_groups_info.add(chat_info.title)
            except Exception:
                disabled_groups_info.add(f"Group ID {group_id}")
            continue

        if isinstance(conditions_data, dict) and conditions_data.get(group_id):
            risks_with_conditions.append(risk)
        else:
            risks_without_conditions.append(risk)

    total_purgeable = len(risks_with_conditions) + len(risks_without_conditions)
    if total_purgeable == 0:
        await update.message.reply_text("The purge feature is currently disabled in all groups where you have posted risks.")
        return ConversationHandler.END

    context.user_data['risks_to_purge_with_conditions'] = risks_with_conditions
    context.user_data['risks_to_purge_without_conditions'] = risks_without_conditions

    confirmation_message = (
        f"🚨 <b>Warning!</b> 🚨\n\n"
        f"You are about to mark <b>{total_purgeable}</b> of your posted risks as 'purged'. "
        "This means they will no longer be available for /taunt or /random, but will remain visible to admins via /seerisk.\n\n"
    )
    if risks_without_conditions:
        confirmation_message += f"• <b>{len(risks_without_conditions)}</b> risks will be purged immediately.\n"
    if risks_with_conditions:
        confirmation_message += f"• <b>{len(risks_with_conditions)}</b> risks will require admin verification based on group conditions.\n"
    if disabled_groups_info:
        confirmation_message += f"\nRisks in these groups cannot be purged as the command is disabled: {', '.join(sorted(list(disabled_groups_info)))}"

    confirmation_message += "\n\nAre you sure you want to proceed?"

    keyboard = [[InlineKeyboardButton("Yes, Purge Them", callback_data='purge_confirm'), InlineKeyboardButton("No, Cancel", callback_data='purge_cancel')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(confirmation_message, reply_markup=reply_markup, parse_mode='HTML')

    return CONFIRM_PURGE

async def send_random_condition(user: User, user_data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Selects a random condition from applicable groups, sends it to the user, and notifies admins."""
    risks_to_purge = user_data.get('risks_to_purge', []) # These are now only the risks with conditions
    if not risks_to_purge:
        # This should not happen if called correctly, but as a safeguard:
        sent_message = await context.bot.send_message(chat_id=user.id, text="No risks requiring a condition were found to purge.")
        await schedule_message_deletion(context, sent_message)
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
        await context.bot.send_message(chat_id=user.id, text="No conditions were found for the relevant groups. Proceeding with purge directly...")

        risks_to_purge = user_data.get('risks_to_purge', [])
        success_count, failure_count = await _delete_and_mark_risks(risks_to_purge, context)

        response_message = (
            f"Purge process is complete.\n"
            f"✅ Successfully deleted {success_count} posts.\n"
            f"❌ Failed to delete {failure_count} posts (they were still marked as purged)."
        )
        await context.bot.send_message(chat_id=user.id, text=response_message)

        # Clean up user_data
        user_data.pop('risks_to_purge', None)
        user_data.pop('risks_to_purge_with_conditions', None)
        user_data.pop('risks_to_purge_without_conditions', None)
        return ConversationHandler.END

    condition = random.choice(applicable_conditions)
    user_data['current_condition'] = condition

    sent_message = await context.bot.send_message(
        chat_id=user.id,
        text=f"An admin has been sent the following condition to verify:\n\n<b>Condition:</b> {html.escape(condition['text'])}\n\nPlease wait for an admin to confirm that you have met this condition.",
        parse_mode='HTML'
    )
    await schedule_message_deletion(context, sent_message)

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
            sent_message = await context.bot.send_message(chat_id=admin_id, text=notification_text, reply_markup=reply_markup, parse_mode='HTML')
            await schedule_message_deletion(context, sent_message)
        except Exception as e:
            logger.warning(f"Failed to send purge verification to admin {admin_id}: {e}")

    return AWAIT_CONDITION_VERIFICATION

async def purge_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles user's confirmation for purging. Deletes posts and marks risks as purged."""
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == 'purge_cancel':
        await query.edit_message_text("Operation cancelled. Your risks have not been changed.")
        # Clean up context
        context.user_data.pop('risks_to_purge_with_conditions', None)
        context.user_data.pop('risks_to_purge_without_conditions', None)
        return ConversationHandler.END

    await query.edit_message_text("Confirmed. Processing request...")

    risks_with_conditions = context.user_data.get('risks_to_purge_with_conditions', [])
    risks_without_conditions = context.user_data.get('risks_to_purge_without_conditions', [])

    # Immediately process risks from groups without conditions
    if risks_without_conditions:
        success_count, failure_count = await _delete_and_mark_risks(risks_without_conditions, context)
        response_message = (
            f"Purge process for groups without conditions is complete.\n"
            f"✅ Successfully deleted {success_count} posts.\n"
            f"❌ Failed to delete {failure_count} posts (they were still marked as purged)."
        )
        await context.bot.send_message(chat_id=user.id, text=response_message)

    # Now, handle risks from groups that have conditions
    if risks_with_conditions:
        context.user_data['risks_to_purge'] = risks_with_conditions # For send_random_condition
        await context.bot.send_message(chat_id=user.id, text="You have risks in groups that require admin verification. Starting that process now...")
        return await send_random_condition(user, context.user_data, context)
    else:
        await context.bot.send_message(chat_id=user.id, text="All applicable risks have been processed.")
        # Clean up context
        context.user_data.pop('risks_to_purge_with_conditions', None)
        context.user_data.pop('risks_to_purge_without_conditions', None)
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

        risks_to_purge = user_data.get('risks_to_purge', [])

        success_count, failure_count = await _delete_and_mark_risks(risks_to_purge, context)

        response_message = (
            f"An admin has approved your request.\n"
            f"✅ Successfully deleted {success_count} posts and marked as purged.\n"
            f"❌ Failed to delete {failure_count} posts (but they were still marked as purged)."
        )
        await context.bot.send_message(chat_id=user_id, text=response_message)

        # Clean up all related data after the final step
        user_data.pop('risks_to_purge', None)
        user_data.pop('current_condition', None)
        user_data.pop('risks_to_purge_with_conditions', None)
        user_data.pop('risks_to_purge_without_conditions', None) # Clean this up too for completeness

    elif decision == 'deny':
        await query.edit_message_text(text=f"{original_message_text}\n\n---\n❌ Denied by {admin_user.mention_html()}", parse_mode='HTML')
        await context.bot.send_message(chat_id=user_id, text="An admin has denied your request. You will now be given a new condition.")

        user_object = await context.bot.get_chat(user_id)
        await send_random_condition(user_object, user_data, context)


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the /post conversation. Asks admin to select a group to post in."""
    user_id = update.effective_user.id
    if update.effective_chat.type != "private":
        sent_message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="The /post command is only available in private chat."
        )
        await schedule_message_deletion(context, sent_message)
        try:
            sent_message = await context.bot.send_message(
                chat_id=user_id,
                text="Please use the /post command here to start creating a post."
            )
            await schedule_message_deletion(context, sent_message)
        except Exception:
            pass # Ignore if user has not started a chat with the bot
        return ConversationHandler.END

    if not is_admin(user_id):
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This is an admin-only command. You are not authorized.")
        await schedule_message_deletion(context, sent_message)
        return ConversationHandler.END

    admin_data = load_admin_data()
    # In Python 3, .get() on a dictionary with a default value is safe.
    # The user_id needs to be a string for JSON key matching.
    user_admin_groups = admin_data.get(str(user_id), [])

    if not user_admin_groups:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="You are not registered as an admin in any groups that I'm aware of. Try running /update in a group where you are an admin.")
        await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="There are no available groups for you to post in. The /post command may be disabled in the groups where you are an admin.")
        await schedule_message_deletion(context, sent_message)
        return ConversationHandler.END

    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Please choose a group to post your message in:", reply_markup=reply_markup)
    await schedule_message_deletion(context, sent_message)
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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This is not a valid media type. Please send a photo or a video.")
        await schedule_message_deletion(context, sent_message)
        return AWAIT_POST_MEDIA # Remain in the same state

    context.user_data['post_media_type'] = media_type
    context.user_data['post_file_id'] = file_id

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Media received. Now, please enter the caption for your post.")
    await schedule_message_deletion(context, sent_message)
    return AWAIT_POST_CAPTION

async def receive_post_caption_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving the caption and shows a preview."""
    caption = update.message.text
    if not caption:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Please provide a caption for your post.")
        await schedule_message_deletion(context, sent_message)
        return AWAIT_POST_CAPTION

    context.user_data['post_caption'] = caption
    media_type = context.user_data['post_media_type']
    file_id = context.user_data['post_file_id']

    # Show preview
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Here is a preview of your post:")
    await schedule_message_deletion(context, sent_message)

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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="There was an error showing the preview. Please try again.")
        await schedule_message_deletion(context, sent_message)
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
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="An error occurred, some information was lost. Please start over with /post."
            )
            await schedule_message_deletion(context, sent_message)
            # Clean up potentially partial data
            for key in ['post_group_id', 'post_media_type', 'post_file_id', 'post_caption']:
                context.user_data.pop(key, None)
            return ConversationHandler.END

        try:
            if media_type == 'photo':
                sent_message = await context.bot.send_photo(group_id, file_id, caption=caption)
            elif media_type == 'video':
                sent_message = await context.bot.send_video(group_id, file_id, caption=caption)
            await schedule_message_deletion(context, sent_message)

            # Send a new message as confirmation
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="✅ Your post has been sent successfully!"
            )
            await schedule_message_deletion(context, sent_message)
        except Exception as e:
            logger.error(f"Failed to send post to group {group_id}: {e}")
            sent_message = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"An error occurred while trying to post. I might not have the right permissions in the target group.\nError: {e}"
            )
            await schedule_message_deletion(context, sent_message)

    elif query.data == 'post_cancel':
        sent_message = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="Post cancelled."
        )
        await schedule_message_deletion(context, sent_message)

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
    'link': {'is_admin': False}, 'inactive': {'is_admin': True}, 'post': {'is_admin': True},
    'setnickname': {'is_admin': True}, 'removenickname': {'is_admin': True}, 'allban': {'is_admin': True}, 'random': {'is_admin': True},
    'enable': {'is_admin': True}, 'update': {'is_admin': True}, 'risk': {'is_admin': False},
    'seerisk': {'is_admin': True}, 'purge': {'is_admin': False}, 'timer': {'is_admin': True}, 'notimer': {'is_admin': True},
    'addcondition': {'is_admin': True}, 'listconditions': {'is_admin': True}, 'removecondition': {'is_admin': True},
}

@command_handler_wrapper(admin_only=False)
async def command_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dynamically lists all available commands based on user's admin status and disabled commands.
    """
    if update.effective_chat.type == "private":
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Please use this command in a group to see the available commands for that group.")
        await schedule_message_deletion(context, sent_message)
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

    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='HTML')
    await schedule_message_deletion(context, sent_message)

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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in group chats.")
        await schedule_message_deletion(context, sent_message)
        return
    if not update.message or not context.args:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /disable <command or hashtag>")
        await schedule_message_deletion(context, sent_message)
        return
    tag = context.args[0].lstrip('#/').lower()
    data = load_hashtag_data()
    # Dynamic command removal
    if tag in data:
        del data[tag]
        save_hashtag_data(data)
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Dynamic command /{tag} has been disabled.")
        await schedule_message_deletion(context, sent_message)
        return
    # Static command disabling
    if tag in COMMAND_MAP:
        group_id = str(update.effective_chat.id)
        disabled = load_disabled_commands()
        disabled.setdefault(group_id, [])
        if tag not in disabled[group_id]:
            disabled[group_id].append(tag)
            save_disabled_commands(disabled)
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{tag} has been disabled in this group. Admins can re-enable it with /enable {tag}.")
            await schedule_message_deletion(context, sent_message)
        else:
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{tag} is already disabled.")
            await schedule_message_deletion(context, sent_message)
        return
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"No such dynamic or static command: /{tag}")
    await schedule_message_deletion(context, sent_message)

@command_handler_wrapper(admin_only=True)
async def enable_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /enable <command> (admin only): Enables a previously disabled command in the group.
    """
    if update.effective_chat.type not in ["group", "supergroup"]:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in group chats.")
        await schedule_message_deletion(context, sent_message)
        return
    if not context.args:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /enable <command>")
        await schedule_message_deletion(context, sent_message)
        return

    command_to_enable = context.args[0].lstrip('/').lower()
    group_id = str(update.effective_chat.id)
    disabled = load_disabled_commands()

    if group_id in disabled and command_to_enable in disabled[group_id]:
        disabled[group_id].remove(command_to_enable)
        if not disabled[group_id]:  # Remove group key if list is empty
            del disabled[group_id]
        save_disabled_commands(disabled)
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{command_to_enable} has been enabled in this group.")
        await schedule_message_deletion(context, sent_message)
    else:
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Command /{command_to_enable} is not currently disabled.")
        await schedule_message_deletion(context, sent_message)

@command_handler_wrapper(admin_only=True)
async def timer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Sets a timer for deleting bot messages in the group.
    Usage: /timer <minutes> (0 to disable)
    """
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="This command can only be used in a group chat.")
        await schedule_message_deletion(context, sent_message)
        return

    if not context.args:
        settings = load_timer_settings()
        group_id_str = str(chat.id)
        current_timer = settings.get(group_id_str, 0)
        if current_timer > 0:
            sent_message = await context.bot.send_message(chat_id=chat.id, text=f"The current message deletion timer is set to {current_timer} minutes. Use `/timer <minutes>` to change it, or `/timer 0` to disable.")
            await schedule_message_deletion(context, sent_message)
        else:
            sent_message = await context.bot.send_message(chat_id=chat.id, text="There is no message deletion timer set for this group. Use `/timer <minutes>` to set one.")
            await schedule_message_deletion(context, sent_message)
        return

    try:
        minutes = int(context.args[0])
    except ValueError:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Invalid time. Please provide a number of minutes.")
        await schedule_message_deletion(context, sent_message)
        return

    if minutes < 0:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Time must be a positive number of minutes.")
        await schedule_message_deletion(context, sent_message)
        return

    settings = load_timer_settings()
    group_id_str = str(chat.id)

    if minutes == 0:
        if group_id_str in settings:
            del settings[group_id_str]
            save_timer_settings(settings)
            sent_message = await context.bot.send_message(chat_id=chat.id, text="Message deletion timer has been disabled for this group.")
            await schedule_message_deletion(context, sent_message)
        else:
            sent_message = await context.bot.send_message(chat_id=chat.id, text="Message deletion timer is already disabled.")
            await schedule_message_deletion(context, sent_message)
    else:
        settings[group_id_str] = minutes
        save_timer_settings(settings)
        sent_message = await context.bot.send_message(chat_id=chat.id, text=f"Bot messages in this group will now be deleted after {minutes} minute(s).")
        await schedule_message_deletion(context, sent_message)

@command_handler_wrapper(admin_only=True)
async def notimer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Prevents a specific message from being deleted by a timer.
    Usage: Reply to one of the bot's messages with /notimer.
    """
    message = update.message
    if not message.reply_to_message:
        sent_message = await context.bot.send_message(chat_id=message.chat.id, text="Please use this command by replying to the message you want to keep.")
        await schedule_message_deletion(context, sent_message)
        return

    # Check if the replied-to message is from this bot
    if not message.reply_to_message.from_user.id == context.bot.id:
        sent_message = await context.bot.send_message(chat_id=message.chat.id, text="This command only works on messages sent by me.")
        await schedule_message_deletion(context, sent_message)
        return

    replied_message = message.reply_to_message
    chat_id = replied_message.chat.id
    message_id = replied_message.message_id
    job_name = f"delete_{chat_id}_{message_id}"

    # Find and remove the scheduled job
    jobs = context.job_queue.get_jobs_by_name(job_name)
    if jobs:
        for job in jobs:
            job.schedule_removal()
        logger.info(f"Removed deletion job {job_name} for message {message_id} in chat {chat_id}.")
        # Also save to the no_delete_ids file as a fallback
        no_delete_ids = load_no_delete_ids()
        no_delete_ids.append({'chat_id': chat_id, 'message_id': message_id})
        save_no_delete_ids(no_delete_ids)
        sent_message = await context.bot.send_message(chat_id=chat_id, text="Okay, I will not delete that message.")
        await schedule_message_deletion(context, sent_message)
    else:
        # If no job was found, it might have already been deleted or was never scheduled.
        # Still, we can add it to the no_delete list just in case the job is about to run.
        no_delete_ids = load_no_delete_ids()
        # Avoid adding duplicates
        if not any(d['message_id'] == message_id for d in no_delete_ids):
            no_delete_ids.append({'chat_id': chat_id, 'message_id': message_id})
            save_no_delete_ids(no_delete_ids)
        sent_message = await context.bot.send_message(chat_id=chat_id, text="This message is now marked to be kept.")
        await schedule_message_deletion(context, sent_message)


@command_handler_wrapper(admin_only=False)
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /admin (as a reply): Forwards a message to the group admins for review.
    """
    message = update.message
    chat = update.effective_chat

    if chat.type not in ['group', 'supergroup']:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="This command can only be used in group chats.")
        await schedule_message_deletion(context, sent_message)
        return

    if not message.reply_to_message:
        sent_message = await context.bot.send_message(chat_id=chat.id, text="Please use this command as a reply to the message you want to report.")
        await schedule_message_deletion(context, sent_message)
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
            sent_message = await context.bot.send_message(
                chat_id=admin.user.id,
                text=report_text,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
            await schedule_message_deletion(context, sent_message)
            notification_sent = True
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin.user.id} for report in group {chat.id}: {e}")

    if notification_sent:
        # Confirm to the user that the report was sent
        sent_message = await context.bot.send_message(chat_id=message.chat.id, text="The admins have been notified.")
        await schedule_message_deletion(context, sent_message)
    else:
        sent_message = await context.bot.send_message(chat_id=message.chat.id, text="Could not notify any admins. Please ensure the bot has the correct permissions.")
        await schedule_message_deletion(context, sent_message)


@command_handler_wrapper(admin_only=False)
async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /link (admin only): Creates a single-use invite link for the group.
    """
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == 'private':
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This command is used to generate an invite link for a group. Please run this command inside the group you want the link for.")
        await schedule_message_deletion(context, sent_message)
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
                sent_message = await context.bot.send_message(
                    chat_id=user.id,
                    text=f"Here is your single-use invite link for the group '{chat.title}':\n{invite_link.invite_link}"
                )
                await schedule_message_deletion(context, sent_message)
                # Confirm in the group chat
                sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="I have sent you a single-use invite link in a private message.")
                await schedule_message_deletion(context, sent_message)
            except Exception as e:
                logger.error(f"Failed to send private message to admin {user.id}: {e}")
                sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="I couldn't send you a private message. Please make sure you have started a chat with me privately first.")
                await schedule_message_deletion(context, sent_message)

        except Exception as e:
            logger.error(f"Failed to create invite link for chat {chat.id}: {e}")
            sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="I was unable to create an invite link. Please ensure I have the 'Invite Users via Link' permission in this group.")
            await schedule_message_deletion(context, sent_message)


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
        sent_message = await context.bot.send_message(
            chat_id=chat.id,
            text=private_start_message,
            disable_web_page_preview=True
        )
        await schedule_message_deletion(context, sent_message)
    else:
        # In a group chat, send a prompt and try to message the user privately
        group_start_message = f"Hey {user.mention_html()}! Please message me in private to get started."
        sent_message = await context.bot.send_message(chat_id=chat.id, text=group_start_message, parse_mode='HTML')
        await schedule_message_deletion(context, sent_message)
        try:
            sent_message = await context.bot.send_message(
                chat_id=user.id,
                text=private_start_message,
                disable_web_page_preview=True
            )
            await schedule_message_deletion(context, sent_message)
        except Exception:
            logger.warning(f"Failed to send private start message to {user.id} who started in group {chat.id}")

#Help command
@command_handler_wrapper(admin_only=False)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the interactive help menu.
    """
    if update.effective_chat.type != "private":
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Please use the /help command in a private chat with me for a better experience.")
        await schedule_message_deletion(context, sent_message)
        return

    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton("General Commands", callback_data='help_general')]
    ]

    # Only show Admin Commands button to admins
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton("Admin Commands", callback_data='help_admin')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_message = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Welcome to the help menu! Please choose a category:",
        reply_markup=reply_markup
    )
    await schedule_message_deletion(context, sent_message)


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
- /link: Generates a single-use invite link for the group.
- /beowned: Information on how to be owned.
- /admin: Request help from admins in a group.
- /risk: Take a risk and let fate decide if your media gets posted. (Private chat only)
- /purge: Marks your posted risks as 'purged', hiding them from /random and /taunt. (Private chat only)
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
- /inactive &lt;days&gt;: Sets up automatic kicking for users who are inactive for a specified number of days (e.g., /inactive 30). Use 0 to disable.
- /timer &lt;minutes&gt;: Sets a timer to automatically delete messages sent by the bot after a certain number of minutes. Use 0 to disable.
- /notimer: Reply to a bot message with this command to prevent it from being auto-deleted by a group timer.

<u>Admin & User Identity</u>
- /update: Refreshes the bot's list of admins for the current group. Run this when admin roles change.
- /setnickname &lt;user&gt; &lt;nickname&gt;: Sets a custom nickname for a user. You can reply to a user or use their ID.
- /removenickname &lt;user&gt;: Removes a user's nickname.

<u>Risk & History</u>
- /seerisk &lt;user_id or @username&gt;: View the risk history of a specific user. Purged risks will be marked.
- /random &lt;percentage&gt;: Sets the percentage chance (0-100) for a random, non-purged risk to be posted automatically every 30 minutes. Use 0 to disable.
- /purge &lt;user_id or @username&gt;: Mark all of a user's risks as purged across all groups. This ignores group conditions but respects if /purge is disabled in a group.

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
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="If you want to be Lion's property, contact @Lionspridechatbot with a head to toe nude picture of yourself and a clear, concise and complete presentation of yourself.")
    await schedule_message_deletion(context, sent_message)

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
            sent_message = await context.bot.send_message(chat_id=message.chat.id, text=response, reply_to_message_id=message.message_id)
            await schedule_message_deletion(context, sent_message)

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
async def schedule_message_deletion(context: ContextTypes.DEFAULT_TYPE, message: Message):
    """
    Schedules a message for deletion if a timer is set for the group.
    """
    chat_id = message.chat_id
    if chat_id < 0:  # Only apply timers in groups
        timer_settings = load_timer_settings()
        group_id_str = str(chat_id)
        timer_minutes = timer_settings.get(group_id_str)

        if timer_minutes and timer_minutes > 0:
            message_id = message.message_id
            job_name = f"delete_{chat_id}_{message_id}"
            context.job_queue.run_once(
                delete_message_callback,
                when=timer_minutes * 60,
                chat_id=chat_id,
                data={'message_id': message_id},
                name=job_name
            )
            logger.debug(f"Scheduled message {message_id} in chat {chat_id} for deletion in {timer_minutes} minutes. Job: {job_name}")

async def delete_message_callback(context: CallbackContext):
    """Deletes the message specified in the job context if it's not marked for no-deletion."""
    job_data = context.job.data
    chat_id = context.job.chat_id
    message_id = job_data['message_id']

    no_delete_ids = load_no_delete_ids()

    message_to_keep = None
    for item in no_delete_ids:
        if item.get('chat_id') == chat_id and item.get('message_id') == message_id:
            message_to_keep = item
            break

    try:
        if message_to_keep:
            logger.info(f"Deletion cancelled for message {message_id} in chat {chat_id} due to /notimer command.")
            return

        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.debug(f"Deleted scheduled message {message_id} in chat {chat_id}")
    except Exception as e:
        logger.warning(f"Failed to delete scheduled message {message_id} in chat {chat_id}: {e}")
    finally:
        # Always try to remove the ID from the list after the job is processed.
        if message_to_keep:
            no_delete_ids.remove(message_to_keep)
            save_no_delete_ids(no_delete_ids)


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
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="This command can only be used in group chats.")
        await schedule_message_deletion(context, sent_message)
        return
    if not context.args or not context.args[0].strip().isdigit():
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Usage: /inactive <days> (0 to disable, 1-99 to enable)")
        await schedule_message_deletion(context, sent_message)
        return
    days = int(context.args[0].strip())
    group_id = str(update.effective_chat.id)
    settings = load_inactive_settings()
    if days == 0:
        settings.pop(group_id, None)
        save_inactive_settings(settings)
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Inactive user kicking is now disabled in this group.")
        await schedule_message_deletion(context, sent_message)
        logger.debug(f"Inactive kicking disabled for group {group_id}")
        return
    if not (1 <= days <= 99):
        sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text="Please provide a number of days between 1 and 99.")
        await schedule_message_deletion(context, sent_message)
        return
    settings[group_id] = days
    save_inactive_settings(settings)
    sent_message = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Inactive user kicking is now enabled for this group. Users inactive for {days} days will be kicked.")
    await schedule_message_deletion(context, sent_message)
    logger.debug(f"Inactive kicking enabled for group {group_id} with threshold {days} days")


async def periodic_random_risk_check(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodically checks each configured group and may post a random risk based on the set percentage.
    """
    logger.debug("Running periodic random risk check...")
    settings = load_random_risk_settings()

    for group_id_str, percentage in settings.items():
        if not (isinstance(percentage, (int, float)) and 0 < percentage <= 100):
            continue

        # Roll the dice
        if random.random() * 100 < percentage:
            logger.info(f"Random risk check passed for group {group_id_str} with {percentage}% chance.")
            try:
                risk_data = load_risk_data()

                group_risks = []
                for user_id, risks in risk_data.items():
                    for risk in risks:
                        if str(risk.get('group_id')) == group_id_str and not risk.get('purged', False):
                            risk['user_id'] = user_id
                            group_risks.append(risk)

                if not group_risks:
                    logger.info(f"Random risk check for group {group_id_str} passed, but no risks were found for this group.")
                    continue

                target_risk = random.choice(group_risks)

                try:
                    user = await context.bot.get_chat(int(target_risk['user_id']))
                    user_mention = user.mention_html()
                except Exception:
                    user_mention = f"user {target_risk['user_id']}"

                caption = f"I feel mean, so lets see what {user_mention} sent me 😂"

                media_type = target_risk['media_type']
                file_id = target_risk['file_id']
                sent_message = None

                if media_type == 'photo':
                    sent_message = await context.bot.send_photo(group_id_str, file_id, caption=caption, parse_mode='HTML')
                elif media_type == 'video':
                    sent_message = await context.bot.send_video(group_id_str, file_id, caption=caption, parse_mode='HTML')
                elif media_type == 'voice':
                    sent_message = await context.bot.send_voice(group_id_str, file_id, caption=caption, parse_mode='HTML')

                if sent_message:
                    await schedule_message_deletion(context, sent_message)

                logger.info(f"Successfully posted random risk {target_risk.get('risk_id')} in group {group_id_str}.")

            except Exception as e:
                logger.error(f"Error during periodic random risk posting for group {group_id_str}: {e}")
        # If the roll fails, we do nothing, ensuring silence.


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
        # Schedule the new random risk job (every 30 minutes)
        app.job_queue.run_repeating(periodic_random_risk_check, interval=1800, first=10)

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
            CONFIRM_PURGE: [CallbackQueryHandler(purge_confirmation_callback, pattern='^purge_confirm$|^purge_cancel$')],
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
    add_command(app, 'random', random_command)
    add_command(app, 'addcondition', addcondition_command)
    add_command(app, 'listconditions', listconditions_command)
    add_command(app, 'removecondition', removecondition_command)
    add_command(app, 'enable', enable_command)
    add_command(app, 'update', update_command)
    add_command(app, 'seerisk', seerisk_command)
    add_command(app, 'timer', timer_command)
    add_command(app, 'notimer', notimer_command)
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
