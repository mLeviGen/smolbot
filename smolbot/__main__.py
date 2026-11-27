import argparse
import asyncio
import logging
import os
import signal
import sqlite3

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, ErrorEvent

from smolbot.bot.access import PrivateAccess
from smolbot.bot.handlers import make_router
from smolbot.bot.views import COMMANDS, Views
from smolbot.config import ConfigError, load_settings
from smolbot.core.alerts import run_alerts
from smolbot.core.hosts import connect
from smolbot.core.monitoring import Monitor
from smolbot.core.operations import Operations
from smolbot.storage import Storage

log = logging.getLogger(__name__)


async def poll_until_stopped(dispatcher, bot, stop):
    polling = asyncio.create_task(
        dispatcher.start_polling(
            bot,
            close_bot_session=False,
            handle_signals=False,
            tasks_concurrency_limit=16,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    )
    waiting = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((polling, waiting), return_when=asyncio.FIRST_COMPLETED)
        if polling in done:
            await polling
        else:
            try:
                await asyncio.wait_for(dispatcher.stop_polling(), timeout=12)
            except (RuntimeError, TimeoutError):
                pass
    finally:
        for task in (polling, waiting):
            if not task.done():
                task.cancel()
        await asyncio.gather(polling, waiting, return_exceptions=True)


async def run(settings, stop):
    connections = connect(settings)
    storage = Storage(settings.database)
    await storage.open()
    operations = Operations(connections, settings, storage)
    monitor = Monitor(connections, settings, storage, operations)
    await monitor.restore()
    access = PrivateAccess(settings.users)
    dispatcher = Dispatcher()
    dispatcher.include_router(make_router(access, Views(monitor, settings, storage), operations))

    @dispatcher.errors()
    async def error_handler(event: ErrorEvent):
        log.warning("Telegram update failed (%s)", type(event.exception).__name__)
        return True

    async with Bot(settings.token) as bot:

        @dispatcher.startup()
        async def register_commands():
            try:
                await bot.set_my_commands(
                    [
                        BotCommand(command=name, description=label)
                        for name, label in COMMANDS.items()
                    ],
                    scope=BotCommandScopeAllPrivateChats(),
                    request_timeout=10,
                )
            except (TelegramNetworkError, TelegramRetryAfter, TelegramServerError) as error:
                log.warning("Command registration unavailable (%s)", type(error).__name__)

        tasks = [
            asyncio.create_task(monitor.run(), name="monitor"),
            asyncio.create_task(run_alerts(bot, storage, settings), name="alerts"),
        ]
        try:
            delay = 2
            while not stop.is_set():
                try:
                    await poll_until_stopped(dispatcher, bot, stop)
                    return
                except (TelegramNetworkError, TelegramRetryAfter, TelegramServerError) as error:
                    log.warning("Telegram polling startup failed (%s)", type(error).__name__)
                    try:
                        async with asyncio.timeout(max(delay, getattr(error, "retry_after", 0))):
                            await stop.wait()
                    except TimeoutError:
                        pass
                    delay = min(60, delay * 2)
        finally:
            for task in tasks:
                task.cancel()
            await access.close()
            await asyncio.gather(*tasks, return_exceptions=True)


async def serve(settings):
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    old_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        old_handlers[sig] = signal.getsignal(sig)
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await run(settings, stop)
    finally:
        for sig, handler in old_handlers.items():
            try:
                loop.remove_signal_handler(sig)
            except NotImplementedError:
                pass
            signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description="SmolBot infrastructure bot")
    parser.add_argument(
        "--check-config", action="store_true", help="validate configuration and exit"
    )
    args = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        parser.exit(2, "SmolBot must run as an unprivileged user.\n")
    try:
        settings = load_settings()
    except ConfigError as error:
        parser.exit(2, f"Configuration error: {error}\n")
    if args.check_config:
        print("Configuration is valid.")
        return 0
    os.umask(0o077)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")
    # Transport or framework diagnostics can include raw request or response details.
    for namespace in ("aiogram", "aiohttp", "dotenv"):
        logging.getLogger(namespace).disabled = True
        logging.getLogger(namespace).setLevel(logging.CRITICAL + 1)
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        return 0
    except (OSError, sqlite3.Error, RuntimeError) as error:
        log.error("SmolBot stopped (%s)", type(error).__name__)
        return 1
    # Keep unexpected framework errors from exposing request data.
    except Exception as error:  # noqa: BLE001
        log.error("SmolBot startup or polling failed (%s)", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
