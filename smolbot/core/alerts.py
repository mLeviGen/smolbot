import asyncio
import logging
import time

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

log = logging.getLogger(__name__)
CATEGORIES = ("infrastructure", "applications", "resources")


async def deliver(bot, storage, settings, now=None):
    now = time.time() if now is None else now
    for event, user, host, category, message in await storage.pending(now):
        until, disabled = await storage.preferences(user)
        if (
            not settings.alerts_enabled
            or user not in settings.users
            or until > now
            or category in disabled
        ):
            await storage.delivered(event, user)
            continue
        try:
            await bot.send_message(user, f"{category.title()} • {host}\n{message}")
        except TelegramRetryAfter as error:
            return error.retry_after
        except TelegramForbiddenError:
            await storage.delivered(event, user)
        except TelegramAPIError as error:
            log.warning("Alert delivery failed (%s)", type(error).__name__)
        else:
            await storage.delivered(event, user)
    return 5


async def run_alerts(bot, storage, settings):
    while True:
        delay = 5
        try:
            delay = await deliver(bot, storage, settings)
        # Keep the outbox running after a delivery or storage error.
        except Exception as error:  # noqa: BLE001
            log.warning("Alert poll failed (%s)", type(error).__name__)
        await asyncio.sleep(max(1, delay))
