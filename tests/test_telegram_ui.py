import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Update

from smolbot.bot.access import PrivateAccess
from smolbot.bot.handlers import make_router
from smolbot.bot.views import Views
from smolbot.core.operations import Operations

from test_config_access import message


class TelegramUiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        settings = SimpleNamespace(users={1}, concurrent_hosts=1)
        self.session = AsyncMock()
        self.bot = Bot("123:synthetic-value", session=self.session)
        self.dispatcher = Dispatcher()
        monitor = SimpleNamespace(connections={}, views={})
        storage = AsyncMock()
        self.dispatcher.include_router(
            make_router(
                PrivateAccess(settings.users),
                Views(monitor, settings, storage),
                Operations({}, settings, storage),
            )
        )

    async def feed_message(self, user, chat, kind):
        data = message(user, chat, kind).model_copy(update={"text": "/start"})
        await self.dispatcher.feed_update(self.bot, Update(update_id=1, message=data))

    async def feed_button(self, user, value):
        callback = CallbackQuery(
            id="callback",
            from_user=message(user, user).from_user,
            chat_instance="instance",
            message=message(100, user),
            data=value,
        )
        await self.dispatcher.feed_update(self.bot, Update(update_id=2, callback_query=callback))
        return [call.args[1] for call in self.session.await_args_list]

    async def test_start_and_inline_navigation(self):
        await self.feed_message(1, 1, "private")
        method = self.session.await_args.args[1]
        self.assertIsInstance(method, SendMessage)
        self.assertIn("0/0 online", method.text)
        token = method.reply_markup.inline_keyboard[0][0].callback_data
        self.assertLessEqual(len(token.encode()), 64)
        calls = await self.feed_button(1, token)
        self.assertIsInstance(calls[-2], AnswerCallbackQuery)
        self.assertIsInstance(calls[-1], EditMessageText)
        self.assertEqual(calls[-1].text, "Select a host")

    async def test_private_allowlist_on_router_and_forged_callbacks(self):
        await self.feed_message(2, 2, "private")
        await self.feed_message(1, -100, "group")
        self.session.assert_not_awaited()
        calls = await self.feed_button(2, "b:aaaaaaaaaaaaaaaa")
        self.assertEqual(calls[-1].text, "Access denied")
        calls = await self.feed_button(1, "b:aaaaaaaaaaaaaaaa")
        self.assertIn("expired", calls[-1].text)
        calls = await self.feed_button(1, "c:aaaaaaaaaaaaaaaaaaaaaa")
        self.assertIn("expired", calls[-1].text)
