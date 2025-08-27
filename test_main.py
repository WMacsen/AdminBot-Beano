import asyncio
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Before importing the main module, we need to set up the environment
# and mock some of the telegram modules it depends on.
# We also need to ensure the TOKEN is set.
os.environ['TELEGRAM_TOKEN'] = 'test_token'
os.environ['OWNER_ID'] = '12345' # Test owner ID

# Mock telegram classes before importing Main
class MockUser:
    def __init__(self, id, full_name, username=None, is_bot=False):
        self.id = id
        self.full_name = full_name
        self.username = username
        self.is_bot = is_bot

    def mention_html(self):
        return f'<a href="tg://user?id={self.id}">{self.full_name}</a>'

class MockChat:
    def __init__(self, id, type, title=None):
        self.id = id
        self.type = type
        self.title = title

class MockMessage:
    def __init__(self, message_id, chat, user, text=None, caption=None, reply_to_message=None, photo=None, video=None, voice=None, document=None):
        self.message_id = message_id
        self.chat = chat
        self.from_user = user
        self.text = text
        self.caption = caption
        self.reply_to_message = reply_to_message
        self.photo = photo
        self.video = video
        self.voice = voice
        self.document = document
        self.reply_text = AsyncMock()
        self.reply_photo = AsyncMock()
        self.reply_video = AsyncMock()

class MockUpdate:
    def __init__(self, message=None, edited_message=None, callback_query=None):
        self.message = message
        self.edited_message = edited_message
        self.callback_query = callback_query
        self.effective_chat = message.chat if message else (callback_query.message.chat if callback_query else None)
        self.effective_user = message.from_user if message else (callback_query.from_user if callback_query else None)

class MockCallbackQuery:
    def __init__(self, id, from_user, message, data=None):
        self.id = id
        self.from_user = from_user
        self.message = message
        self.data = data
        self.answer = AsyncMock()
        self.edit_message_text = AsyncMock()
        self.edit_message_caption = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()

class MockBot:
    def __init__(self):
        self.get_chat_member = AsyncMock()
        self.get_chat_administrators = AsyncMock()
        self.delete_message = AsyncMock()
        self.send_message = AsyncMock()
        self.send_photo = AsyncMock()
        self.send_video = AsyncMock()
        self.send_voice = AsyncMock()
        self.forward_message = AsyncMock()
        self.create_chat_invite_link = AsyncMock()
        self.ban_chat_member = AsyncMock()
        self.unban_chat_member = AsyncMock()
        self.get_chat = AsyncMock()

class MockContext:
    def __init__(self, bot):
        self.bot = bot
        self.args = []
        self.user_data = {}
        self.chat_data = {}
        self.job_queue = MagicMock()
        self.job_queue.run_once = MagicMock()

# Now it's safe to import the main module
import Main as bot_main

# =============================
# Test Case Class
# =============================

class TestBot(unittest.TestCase):

    def setUp(self):
        """Set up for each test."""
        # Clean up all data files before each test
        self.test_files = [
            bot_main.HASHTAG_DATA_FILE,
            bot_main.ADMIN_DATA_FILE,
            bot_main.ADMIN_NICKNAMES_FILE,
            bot_main.RISK_DATA_FILE,
            bot_main.ACTIVITY_DATA_FILE,
            bot_main.INACTIVE_SETTINGS_FILE,
            bot_main.DISABLED_COMMANDS_FILE
        ]
        for f in self.test_files:
            if os.path.exists(f):
                os.remove(f)

        # Reset OWNER_ID for tests
        bot_main.OWNER_ID = '12345'

        # Mock users
        self.owner_user = MockUser(12345, "Test Owner", "testowner")
        self.admin_user = MockUser(54321, "Test Admin", "testadmin")
        self.normal_user = MockUser(67890, "Test User", "testuser")

        # Mock chats
        self.group_chat = MockChat(-1001, "supergroup", "Test Group")
        self.private_chat = MockChat(self.normal_user.id, "private")

    def tearDown(self):
        """Clean up after each test."""
        for f in self.test_files:
            if os.path.exists(f):
                os.remove(f)

    def test_owner_id_is_set(self):
        """Test that the OWNER_ID is loaded from the environment."""
        self.assertEqual(bot_main.OWNER_ID, '12345')

    def test_start_command_private(self):
        """Test /start command in a private chat."""
        message = MockMessage(1, self.private_chat, self.owner_user, text="/start")
        update = MockUpdate(message=message)
        context = MockContext(MockBot())

        asyncio.run(bot_main.start_command(update, context))

        context.bot.send_message.assert_called_once()
        self.assertIn("Hello! I'm a bot", context.bot.send_message.call_args[1]['text'])

    def test_hashtag_handler(self):
        """Test the hashtag message handler."""
        message = MockMessage(1, self.group_chat, self.normal_user, text="Here is a #test post")
        update = MockUpdate(message=message)
        context = MockContext(MockBot())
        context.bot.get_chat_administrators.return_value = [] # No admins to notify

        asyncio.run(bot_main.hashtag_message_handler(update, context))

        data = bot_main.load_hashtag_data()
        self.assertIn("test", data)
        self.assertEqual(len(data["test"]), 1)
        self.assertEqual(data["test"][0]['text'], "Here is a #test post")

    def test_update_command(self):
        """Test the /update command to refresh admin list."""
        message = MockMessage(1, self.group_chat, self.admin_user, text="/update")
        update = MockUpdate(message=message)
        context = MockContext(MockBot())

        # Mock the API calls the command depends on
        context.bot.get_chat_member.return_value = MagicMock(status=bot_main.ChatMemberStatus.ADMINISTRATOR)
        mock_telegram_admins = [
            MagicMock(user=self.owner_user, status=bot_main.ChatMemberStatus.OWNER),
            MagicMock(user=self.admin_user, status=bot_main.ChatMemberStatus.ADMINISTRATOR)
        ]
        context.bot.get_chat_administrators.return_value = mock_telegram_admins

        asyncio.run(bot_main.update_command(update, context))

        admin_data = bot_main.load_admin_data()
        self.assertIn(str(self.owner_user.id), admin_data)
        self.assertIn(str(self.admin_user.id), admin_data)
        self.assertIn(str(self.group_chat.id), admin_data[str(self.admin_user.id)])

        context.bot.send_message.assert_called_once()
        self.assertIn("Admin list updated", context.bot.send_message.call_args[1]['text'])

    def test_set_and_remove_nickname(self):
        """Test setting and removing a nickname for a user."""
        context = MockContext(MockBot())

        # Mock get_chat_member to return different member objects based on user_id
        async def get_chat_member_side_effect(chat_id, user_id):
            if user_id == self.owner_user.id:
                member = MagicMock(status=bot_main.ChatMemberStatus.OWNER)
                member.user = self.owner_user
                return member
            elif user_id == self.admin_user.id:
                member = MagicMock(status=bot_main.ChatMemberStatus.ADMINISTRATOR)
                member.user = self.admin_user
                return member
            return MagicMock(status=bot_main.ChatMemberStatus.MEMBER)
        context.bot.get_chat_member.side_effect = get_chat_member_side_effect

        # 1. Set nickname
        replied_message = MockMessage(2, self.group_chat, self.admin_user, text="some message")
        message = MockMessage(1, self.group_chat, self.owner_user, text="/setnickname CoolAdmin", reply_to_message=replied_message)
        update = MockUpdate(message=message)
        context.args = ["CoolAdmin"]

        asyncio.run(bot_main.setnickname_command(update, context))

        nicknames = bot_main.load_admin_nicknames()
        self.assertEqual(nicknames[str(self.admin_user.id)], "CoolAdmin")
        context.bot.send_message.assert_called_once_with(chat_id=self.group_chat.id, text=f"Nickname for {self.admin_user.mention_html()} has been set to 'CoolAdmin'.", parse_mode='HTML')

        # 2. Remove nickname
        context.bot.send_message.reset_mock()
        remove_message = MockMessage(3, self.group_chat, self.owner_user, text="/removenickname", reply_to_message=replied_message)
        remove_update = MockUpdate(message=remove_message)
        context.args = []

        asyncio.run(bot_main.removenickname_command(remove_update, context))

        nicknames = bot_main.load_admin_nicknames()
        self.assertNotIn(str(self.admin_user.id), nicknames)
        context.bot.send_message.assert_called_once_with(chat_id=self.group_chat.id, text=f"Nickname for {self.admin_user.mention_html()} has been removed.", parse_mode='HTML')

    def test_disable_and_enable_command(self):
        """Test disabling and enabling a command in a group."""
        # 1. Disable command
        message = MockMessage(1, self.group_chat, self.admin_user, text="/disable beowned")
        update = MockUpdate(message=message)
        context = MockContext(MockBot())
        context.args = ["beowned"]
        context.bot.get_chat_member.return_value = MagicMock(status='administrator') # User is admin

        asyncio.run(bot_main.disable_command(update, context))

        disabled = bot_main.load_disabled_commands()
        self.assertIn('beowned', disabled[str(self.group_chat.id)])
        context.bot.send_message.assert_called_with(chat_id=self.group_chat.id, text="Command /beowned has been disabled in this group. Admins can re-enable it with /enable beowned.")

        # 2. Enable command
        # Note: We re-use the same context to ensure the bot object is the same,
        # but create a new message and update.
        message_enable = MockMessage(2, self.group_chat, self.admin_user, text="/enable beowned")
        update_enable = MockUpdate(message=message_enable)
        context.args = ["beowned"] # Set args for the enable command

        asyncio.run(bot_main.enable_command(update_enable, context))

        disabled = bot_main.load_disabled_commands()
        self.assertNotIn(str(self.group_chat.id), disabled) # Key should be gone if list is empty
        context.bot.send_message.assert_called_with(chat_id=self.group_chat.id, text="Command /beowned has been enabled in this group.")

    def test_inactive_command(self):
        """Test the /inactive command."""
        message = MockMessage(1, self.group_chat, self.admin_user, text="/inactive 30")
        update = MockUpdate(message=message)
        context = MockContext(MockBot())
        context.args = ["30"]
        context.bot.get_chat_member.return_value = MagicMock(status='administrator')

        asyncio.run(bot_main.inactive_command(update, context))

        settings = bot_main.load_inactive_settings()
        self.assertEqual(settings[str(self.group_chat.id)], 30)

        # Test disabling
        context.args = ["0"]
        asyncio.run(bot_main.inactive_command(update, context))
        settings = bot_main.load_inactive_settings()
        self.assertNotIn(str(self.group_chat.id), settings)

    def _run_risk_conversation_start(self, context):
        """Helper to start the risk conversation and select a group."""
        # Setup admin data so the bot knows about the group
        bot_main.save_admin_data({str(self.admin_user.id): [str(self.group_chat.id)]})

        # 1. Start /risk command in private
        message = MockMessage(1, self.private_chat, self.normal_user, text="/risk")
        update = MockUpdate(message=message)
        context.bot.get_chat.return_value = self.group_chat

        state = asyncio.run(bot_main.risk_command(update, context))
        self.assertEqual(state, bot_main.SELECT_GROUP)
        message.reply_text.assert_called_once()
        self.assertIn("Choose a group", message.reply_text.call_args[0][0])

        # 2. User selects a group
        callback_query = MockCallbackQuery(1, self.normal_user, message, data=f"risk_group_{self.group_chat.id}")
        update = MockUpdate(callback_query=callback_query)

        state = asyncio.run(bot_main.select_group_callback(update, context))
        self.assertEqual(state, bot_main.AWAIT_MEDIA)
        self.assertEqual(context.user_data['risk_group_id'], str(self.group_chat.id))
        callback_query.edit_message_text.assert_called_once()
        self.assertIn("Please send the media", callback_query.edit_message_text.call_args[1]['text'])

    def test_risk_conversation_lucky(self):
        """Test the 'lucky' path of the /risk conversation."""
        context = MockContext(MockBot())
        self._run_risk_conversation_start(context)

        # 3. User sends a photo and is lucky
        photo_message = MockMessage(2, self.private_chat, self.normal_user, photo=[MagicMock(file_id="photofile123")])
        update = MockUpdate(message=photo_message)

        with patch('random.choice', return_value=False): # Lucky case
            state = asyncio.run(bot_main.receive_media_handler(update, context))
            self.assertEqual(state, bot_main.ConversationHandler.END)
            photo_message.reply_text.assert_called_with("You were lucky! Your photo will not be posted... this time.")
            context.bot.send_photo.assert_not_called()

        risk_data = bot_main.load_risk_data()
        self.assertIn(str(self.normal_user.id), risk_data)
        self.assertEqual(risk_data[str(self.normal_user.id)][0]['posted'], False)

    def test_risk_conversation_unlucky(self):
        """Test the 'unlucky' path of the /risk conversation."""
        context = MockContext(MockBot())
        self._run_risk_conversation_start(context)

        # 3. User sends a photo and is unlucky
        photo_message = MockMessage(2, self.private_chat, self.normal_user, photo=[MagicMock(file_id="photofile123")])
        update = MockUpdate(message=photo_message)

        with patch('random.choice', return_value=True): # Unlucky case
            state = asyncio.run(bot_main.receive_media_handler(update, context))
            self.assertEqual(state, bot_main.ConversationHandler.END)
            photo_message.reply_text.assert_called_with("You were not lucky... your media has been posted to the group.")
            context.bot.send_photo.assert_called_once()

        risk_data = bot_main.load_risk_data()
        self.assertIn(str(self.normal_user.id), risk_data)
        self.assertEqual(risk_data[str(self.normal_user.id)][0]['posted'], True)


if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
