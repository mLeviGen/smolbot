import asyncio
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message


def authorized(event, users):
    message = event.message if isinstance(event, CallbackQuery) else event
    return (
        isinstance(message, Message)
        and event.from_user is not None
        and message.chat.type == ChatType.PRIVATE
        and message.chat.id == event.from_user.id
        and event.from_user.id in users
    )


class PrivateAccess(BaseMiddleware):
    def __init__(self, users):
        self.users = users
        self.inflight = set()

    async def __call__(self, handler, event, data):
        if not authorized(event, self.users):
            if isinstance(event, CallbackQuery):
                await event.answer("Access denied", show_alert=True)
            return None
        task = asyncio.current_task()
        self.inflight.add(task)
        try:
            return await handler(event, data)
        finally:
            self.inflight.discard(task)

    async def close(self):
        tasks = tuple(self.inflight)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(frozen=True)
class Page:
    route: str
    host: str = ""
    app: str = ""
    number: int = 0
    value: str = ""


class Buttons:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.entries = OrderedDict()

    def create(self, user, page):
        now = self.clock()
        while self.entries and (
            len(self.entries) >= 4096 or next(iter(self.entries.values()))[0] <= now
        ):
            self.entries.popitem(last=False)
        token = secrets.token_urlsafe(12)
        self.entries[token] = (now + 600, user, page)
        return token

    def get(self, user, token):
        expiry, owner, page = self.entries.get(token, (0, 0, None))
        return page if owner == user and expiry > self.clock() else None
