import asyncio
import tempfile
import unittest
from pathlib import Path

from aiogram.types import CallbackQuery, Chat, Message, User

from smolbot.bot.access import Buttons, Page, PrivateAccess, authorized
from smolbot.config import ConfigError, load_settings


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cwd = Path(self.directory.name).resolve()
        self.hosts = self.cwd / "hosts.toml"
        self.hosts.write_text(
            '[[hosts]]\nid="test"\nname="Test"\ntransport="local"\nsnt_path="snt"\n'
        )
        self.env = {"BOT_TOKEN": "123:synthetic-value", "ALLOWED_USER_IDS": "123, 456"}

    def test_defaults_and_paths(self):
        config = load_settings(self.env, cwd=self.cwd)
        self.assertEqual(config.users, {123, 456})
        self.assertEqual(config.poll_interval, 30)
        self.assertEqual(config.database, self.cwd / "data" / "smolbot.db")
        self.assertEqual(config.hosts[0].snt_path, "snt")
        self.assertNotIn(self.env["BOT_TOKEN"], repr(config))

    def test_invalid_environment(self):
        for field, value in (
            ("BOT_TOKEN", ""),
            ("ALLOWED_USER_IDS", ""),
            ("ALLOWED_USER_IDS", "name"),
            ("ALLOWED_USER_IDS", str(2**52)),
            ("COMMAND_TIMEOUT_SECONDS", "-1"),
            ("SSH_CONNECT_TIMEOUT_SECONDS", "61"),
            ("CPU_CRITICAL", "20"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ConfigError):
                load_settings(self.env | {field: value}, cwd=self.cwd)

    def test_duplicate_host(self):
        original = self.hosts.read_text()
        self.hosts.write_text(original + original)
        with self.assertRaisesRegex(ConfigError, "Duplicate"):
            load_settings(self.env, cwd=self.cwd)

    def test_host_errors_do_not_leak_config(self):
        self.hosts.write_text("[[hosts]]\nidentity_file=should-not-print")
        with self.assertRaises(ConfigError) as error:
            load_settings(self.env, cwd=self.cwd)
        self.assertNotIn("should-not-print", str(error.exception))

    def test_multihost_ssh_paths(self):
        self.hosts.write_text(self.hosts.read_text() + """
[[hosts]]
id="second"
name="Second"
transport="ssh"
address="server.example"
username="monitor"
identity_file="keys/bot"
known_hosts="keys/known_hosts"
snt_path="/home/monitor/.local/bin/snt"
[[hosts]]
id="third"
name="Third"
transport="ssh"
address="::1"
username="monitor"
identity_file="keys/bot"
snt_path="/usr/bin/snt"
enabled=false
""")
        hosts = load_settings(self.env, cwd=self.cwd).hosts
        self.assertEqual([h.id for h in hosts], ["test", "second", "third"])
        self.assertEqual(Path(hosts[1].identity_file), self.cwd / "keys" / "bot")
        self.assertFalse(hosts[2].enabled)


def message(user=1, chat=1, kind="private"):
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=chat, type=kind),
        from_user=User(id=user, is_bot=False, first_name="Test"),
    )


class AuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def test_private_message_only(self):
        self.assertTrue(authorized(message(), {1}))
        self.assertFalse(authorized(message(2, 2), {1}))
        self.assertFalse(authorized(message(1, 1, "group"), {1}))
        self.assertFalse(authorized(message(1, 2), {1}))
        self.assertFalse(authorized(message(), set()))

    def test_callback_uses_clicker_and_chat(self):
        query = CallbackQuery(
            id="query",
            from_user=message().from_user,
            chat_instance="private",
            message=message(99, 1),
            data="forged",
        )
        self.assertTrue(authorized(query, {1}))
        inline = query.model_copy(update={"message": None, "inline_message_id": "inline"})
        self.assertFalse(authorized(inline, {1}))

    def test_opaque_buttons_expire_and_are_user_bound(self):
        now = [0]
        buttons = Buttons(clock=lambda: now[0])
        page = Page("app", "host", "application")
        token = buttons.create(1, page)
        self.assertIsNone(buttons.get(2, token))
        self.assertIsNone(buttons.get(1, "forged"))
        self.assertEqual(buttons.get(1, token), page)
        now[0] = 600
        self.assertIsNone(buttons.get(1, token))

    async def test_middleware_shutdown(self):
        access = PrivateAccess({1})
        started = asyncio.Event()

        async def handler(event, data):
            started.set()
            await asyncio.Future()

        self.assertIsNone(await access(handler, message(2, 2), {}))
        task = asyncio.create_task(access(handler, message(), {}))
        await started.wait()
        await access.close()
        self.assertTrue(task.cancelled())
        self.assertFalse(access.inflight)
