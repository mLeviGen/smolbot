import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from smolbot.__main__ import poll_until_stopped


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_requests_graceful_polling_shutdown(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def polling(*args, **kwargs):
            started.set()
            await stopped.wait()

        async def stop_polling():
            stopped.set()

        dispatcher = SimpleNamespace(
            start_polling=AsyncMock(side_effect=polling),
            stop_polling=AsyncMock(side_effect=stop_polling),
            resolve_used_update_types=lambda: ["message", "callback_query"],
        )
        stop = asyncio.Event()
        task = asyncio.create_task(poll_until_stopped(dispatcher, object(), stop))
        await asyncio.wait_for(started.wait(), 2)
        stop.set()
        await asyncio.wait_for(task, 2)
        dispatcher.stop_polling.assert_awaited_once()
        self.assertTrue(stopped.is_set())
