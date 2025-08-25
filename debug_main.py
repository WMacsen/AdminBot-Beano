import logging
import os
import json
import re
import asyncio
import uuid
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler, ConversationHandler, CallbackQueryHandler
from telegram.constants import ChatMemberStatus

# =========================
# Logging Configuration
# =========================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot_debug.log", encoding='utf-8'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# =========================
# Configuration
# =========================
TOKEN = os.environ.get('TELEGRAM_TOKEN', 'YOUR_TOKEN_HERE')
HASHTAG_DATA_FILE = 'hashtag_data.json'
ADMIN_DATA_FILE = 'admins.json'
GAMES_DATA_FILE = 'games.json'
POINTS_DATA_FILE = 'points.json'
OWNER_ID = 7237569475
DISABLED_COMMANDS_FILE = 'disabled_commands.json'
BOT_USERNAME: str = '@MasterBeanoBot'

# =============================
# Data Management Functions
# =============================
def load_data(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            try: return json.load(f)
            except json.JSONDecodeError: return {}
    return {}

def save_data(data, filepath):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_hashtag_data(): return load_data(HASHTAG_DATA_FILE)
def save_hashtag_data(data): save_data(data, HASHTAG_DATA_FILE)
def load_admin_data():
    data = load_data(ADMIN_DATA_FILE)
    data.setdefault('admins', [])
    if str(OWNER_ID) not in data['admins']: data['admins'].append(str(OWNER_ID))
    data['owner'] = str(OWNER_ID)
    return data
def load_admin_nicknames(): return load_data('admin_nicknames.json')
def load_disabled_commands(): return load_data(DISABLED_COMMANDS_FILE)
def load_games_data(): return load_data(GAMES_DATA_FILE)
def save_games_data(data): save_data(data, GAMES_DATA_FILE)
def get_user_points(group_id, user_id): return load_data(POINTS_DATA_FILE).get(str(group_id), {}).get(str(user_id), 0)

# =============================
# Helpers & Decorators
# =============================
def is_admin(user_id):
    admin_data = load_admin_data()
    return str(user_id) in admin_data['admins']

def get_display_name(user_id: int, full_name: str) -> str:
    if not is_admin(user_id): return "fag"
    return load_admin_nicknames().get(str(user_id), full_name)

def command_handler_wrapper(admin_only=False):
    def decorator(func):
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            if not update.effective_user or not update.message: return
            user, chat = update.effective_user, update.effective_chat
            try:
                if chat.type in ['group', 'supergroup']:
                    command_name = func.__name__.replace('_command', '')
                    if command_name in set(load_disabled_commands().get(str(chat.id), [])): return
                if admin_only and not is_admin(user.id):
                    await update.message.reply_text(f"Warning: {user.mention_html()}, you are not authorized.", parse_mode='HTML')
                    return
                await func(update, context, *args, **kwargs)
            finally:
                if chat.type in ['group', 'supergroup']:
                    try: await context.bot.delete_message(chat.id, update.message.message_id)
                    except Exception: pass
        return wrapper
    return decorator

# =============================
# Hashtag Feature
# =============================
async def hashtag_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message: return
    text = message.text or message.caption or ''
    hashtags = re.findall(r'#(\w+)', text)
    if not hashtags: return
    data = load_hashtag_data()
    for tag in hashtags:
        tag = tag.lower()
        entry = {'user_id': message.from_user.id, 'text': text} # Simplified for test
        data.setdefault(tag, []).append(entry)
    save_hashtag_data(data)
    await message.reply_text(f"Saved under: {', '.join('#'+t for t in hashtags)}")

async def dynamic_hashtag_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    command = update.message.text[1:].split()[0].lower()
    if command in context.bot_data.get("COMMAND_MAP", {}): return
    data = load_hashtag_data()
    if command not in data:
        await update.message.reply_text(f"No data found for #{command}.")
        return
    await update.message.reply_text(f"Data found for #{command}: {data[command]}")

# =============================
# Game Setup Conversation
# =============================
GAME_SELECTION, ROUND_SELECTION, STAKE_TYPE_SELECTION, STAKE_SUBMISSION_POINTS, STAKE_SUBMISSION_MEDIA, CONFIRMATION = range(6)

@command_handler_wrapper(admin_only=False)
async def newgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Please use this command as a reply.")
        return
    challenger, opponent = update.effective_user, update.message.reply_to_message.from_user
    if challenger.id == opponent.id:
        await update.message.reply_text("You cannot challenge yourself.")
        return
    game_id = str(uuid.uuid4())
    games_data = load_games_data()
    games_data[game_id] = {"group_id": update.effective_chat.id, "challenger_id": challenger.id, "opponent_id": opponent.id, "status": "pending_game_selection"}
    save_games_data(games_data)
    keyboard = [[InlineKeyboardButton("Start Game Setup", callback_data=f"start_game_setup_{game_id}")]]
    try:
        await context.bot.send_message(challenger.id, "Let's set up your game!", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Failed to send PM for new game: {e}")

async def start_game_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game_id = query.data.split('_')[-1]
    context.user_data['game_id'] = game_id
    keyboard = [[InlineKeyboardButton("Dice", callback_data='game_dice')], [InlineKeyboardButton("Connect Four", callback_data='game_c4')]]
    await query.edit_message_text("Select a game:", reply_markup=InlineKeyboardMarkup(keyboard))
    return GAME_SELECTION

async def game_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    game_id = context.user_data['game_id']
    games_data = load_games_data()
    games_data[game_id]['game_type'] = query.data
    save_games_data(games_data)
    keyboard = [[InlineKeyboardButton("Points", callback_data='stake_points')], [InlineKeyboardButton("Media", callback_data='stake_media')]]
    await query.edit_message_text("What to stake?", reply_markup=InlineKeyboardMarkup(keyboard))
    return STAKE_TYPE_SELECTION

async def stake_type_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'stake_points':
        await query.edit_message_text("How many points?")
        return STAKE_SUBMISSION_POINTS
    elif query.data == 'stake_media':
        await query.edit_message_text("Send the media.")
        return STAKE_SUBMISSION_MEDIA

async def stake_submission_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Stake set as points.")
    return ConversationHandler.END

async def stake_submission_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Stake set as media.")
    return ConversationHandler.END

async def cancel_game_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("Cancelled.")
    return ConversationHandler.END

def get_game_setup_handler():
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(start_game_setup, pattern='^start_game_setup_')],
        states={
            GAME_SELECTION: [CallbackQueryHandler(game_selection)],
            STAKE_TYPE_SELECTION: [CallbackQueryHandler(stake_type_selection)],
            STAKE_SUBMISSION_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, stake_submission_points)],
            STAKE_SUBMISSION_MEDIA: [MessageHandler(filters.ATTACHMENT, stake_submission_media)],
        },
        fallbacks=[CallbackQueryHandler(cancel_game_setup, pattern='^cancel_game_')],
        per_message=False
    )

# =========================
# Main Execution
# =========================
if __name__ == '__main__':
    logger.info('Starting DEBUG bot...')
    app = Application.builder().token(TOKEN).build()

    app.bot_data["COMMAND_MAP"] = {'newgame': {}}

    # Add game handlers
    app.add_handler(CommandHandler('newgame', newgame_command))
    app.add_handler(get_game_setup_handler())

    # Add hashtag handlers
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION | filters.ATTACHMENT) & ~filters.COMMAND, hashtag_message_handler))
    app.add_handler(MessageHandler(filters.COMMAND, dynamic_hashtag_command), group=1)

    logger.info('Polling...')
    app.run_polling()
