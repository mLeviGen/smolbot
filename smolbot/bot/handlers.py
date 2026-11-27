import asyncio
import logging
import re
import time
from dataclasses import replace

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from smolbot.bot.access import Buttons, Page
from smolbot.bot.views import COMMANDS, HOME, PHASE, SHORTCUTS
from smolbot.core.alerts import CATEGORIES
from smolbot.core.snt import OBSERVATION_ERRORS, Action, SntError

log = logging.getLogger(__name__)
CALLBACK = re.compile(r"([bc]):([A-Za-z0-9_-]{16,22})")


def make_router(access, views, operations):
    router = Router()
    router.message.outer_middleware(access)
    router.callback_query.outer_middleware(access)
    buttons = Buttons()
    refresh_lock = asyncio.Lock()

    def keyboard(user, rows):
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=label[:110],
                        callback_data=(
                            target
                            if isinstance(target, str)
                            else "b:" + buttons.create(user, target)
                        ),
                    )
                    for label, target in row
                ]
                for row in rows
            ]
        )

    async def show(message, user, text, rows, *, edit=True):
        # Telegram text is plain. Cap by UTF-16 code units for payloads containing emoji.
        text = text.encode("utf-16-le", errors="replace")[:8000].decode(
            "utf-16-le", errors="ignore"
        )
        markup = keyboard(user, rows)
        if edit:
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    return
                if "message can't be edited" not in str(error).lower():
                    raise
        await message.answer(text, reply_markup=markup)

    async def render(message, user, page, *, edit=True):
        text, rows = await views.render(user, page)
        await show(message, user, text, rows, edit=edit)

    @router.message(Command(*COMMANDS))
    async def command_handler(message, command: CommandObject):
        name = command.command
        if name in SHORTCUTS:
            page = Page("apps", value=SHORTCUTS[name])
        elif name == "system":
            page = Page("hosts", value="system")
        else:
            page = Page("home" if name in {"start", "status"} else name)
        await render(message, message.from_user.id, page, edit=False)

    @router.callback_query(F.data)
    async def callback_handler(query):
        user = query.from_user.id
        message = query.message
        match = CALLBACK.fullmatch(query.data)
        if not match:
            await query.answer("Menu expired. Use /start.", show_alert=True)
            return
        kind, token = match.groups()
        if kind == "b":
            page = buttons.get(user, token)
            if page is None:
                await query.answer("Menu expired. Use /start.", show_alert=True)
                return
        else:
            try:
                item = operations.consume(user, token)
            except SntError as error:
                await query.answer(str(error), show_alert=True)
                return

        try:
            await query.answer()
        except TelegramBadRequest:
            # A stale Telegram acknowledgement should not replay work on its own.
            return
        try:
            if kind == "c":
                back = [[("Application", Page("app", item.host, item.app))]]

                async def report(phase):
                    try:
                        async with asyncio.timeout(8):
                            await show(
                                message, user, f"{item.app} · {item.host}\n\n{PHASE[phase]}", back
                            )
                    except (TelegramAPIError, TimeoutError) as error:
                        log.warning("Operation update failed (%s)", type(error).__name__)

                await operations.execute(item, report)
                await views.monitor.refresh(item.host, manual=True)
                return
            if page.route == "request":
                action = Action(page.value)
                available = (views.monitor.views.get(page.host, {}).get("status") or {}).get(
                    "apps", {}
                )
                if page.app not in available:
                    raise SntError("Application unavailable. Refresh the host first.")
                confirmation = operations.issue(user, page.host, page.app, action)
                await show(
                    message,
                    user,
                    f"{action.value.title()} application?\n\nHost: {page.host}\nApp: {page.app}",
                    [
                        [
                            ("Confirm", "c:" + confirmation),
                            ("Cancel", replace(page, route="cancel", value=confirmation)),
                        ]
                    ],
                )
                return
            if page.route == "cancel":
                try:
                    operations.consume(user, page.value)
                except SntError:
                    pass
                page = Page("app", page.host, page.app)
            if page.route == "refresh":
                await views.monitor.refresh(page.host, manual=True)
                page = replace(page, route=page.value, value="")
            if page.route == "refresh_all":
                if not refresh_lock.locked():
                    async with refresh_lock, asyncio.TaskGroup() as tasks:
                        for host in views.monitor.connections:
                            tasks.create_task(views.monitor.refresh(host, manual=True))
                page = Page("home")
            if page.route == "toggle" and page.value in CATEGORIES:
                await views.storage.set_preferences(user, category=page.value)
                page = Page("alerts")
            if page.route == "mute":
                await views.storage.set_preferences(
                    user, mute=int(time.time()) + 3600 if page.value == "3600" else 0
                )
                page = Page("alerts")
            if page.route == "read_log":
                client = operations.client(user, page.host)
                content = await client.logs(page.app, page.number)
                header = f"{page.app} · {page.host} · Last {page.number} log lines"
                back = [[("Back", Page("logs", page.host, page.app))]]
                content = content.strip()
                if len(content.encode("utf-16-le")) > 6500:
                    await message.answer_document(
                        BufferedInputFile(content.encode("utf-8"), filename="smolbot-log.txt"),
                        caption=header,
                    )
                    await show(message, user, header + "\nLog sent as a file.", back)
                else:
                    await show(message, user, header + "\n\n" + (content or "No log output."), back)
                return
            await render(message, user, page)
        except OBSERVATION_ERRORS as error:
            await show(message, user, str(error), HOME)

    return router
