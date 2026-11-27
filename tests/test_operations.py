import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from smolbot.config import Host
from smolbot.core.operations import Operations, outcome_for
from smolbot.core.snt import Action, App, Snapshot, SntClient, SntError
from smolbot.transports.base import TransportError


class OperationsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = App("worker", "running", True, pid=100, commit="first", last_check="T1")
        self.snapshot = Snapshot("2.1.0", True, 50, None, {"worker": self.app})
        self.host = Host("test", "Test", "local", "snt")
        self.client = SntClient(self.host, AsyncMock(), (), frozenset())
        self.client.status = AsyncMock(return_value=self.snapshot)
        self.client.submit = AsyncMock(return_value="queued")
        self.storage = AsyncMock()
        self.now = 10
        self.operations = Operations(
            {"test": SimpleNamespace(host=self.host, client=self.client)},
            SimpleNamespace(users={1, 2}, concurrent_hosts=2, operation_timeout=5),
            self.storage,
            clock=lambda: self.now,
        )

    def test_confirmation_bound_single_use_and_expiring(self):
        token = self.operations.issue(1, "test", "worker", Action.RESTART)
        with self.assertRaises(SntError):
            self.operations.consume(2, token)
        item = self.operations.consume(1, token)
        self.assertEqual((item.host, item.app, item.action), ("test", "worker", Action.RESTART))
        with self.assertRaises(SntError):
            self.operations.consume(1, token)
        token = self.operations.issue(1, "test", "worker", Action.RESTART)
        self.now += 60
        with self.assertRaises(SntError):
            self.operations.consume(1, token)

    def test_unauthorized_host_and_self_protection(self):
        for user, host, app in (
            (99, "test", "worker"),
            (1, "fake", "worker"),
            (1, "test", "SMOLBOT"),
        ):
            with self.subTest(user=user, host=host, app=app), self.assertRaises(SntError):
                self.operations.issue(user, host, app, Action.STOP)

    def test_operation_outcomes(self):
        waiting = replace(self.app, status="waiting", pid=None)
        stopped = replace(self.app, status="disabled", enabled=False, pid=None)
        updated = replace(self.app, pid=200, commit="next", last_check="T2")
        self.assertIsNone(outcome_for(Action.RESTART, self.app, self.app, self.app))
        self.assertEqual(outcome_for(Action.RESTART, self.app, updated, self.app), "confirmed")
        self.assertEqual(outcome_for(Action.START, waiting, self.app, waiting), "confirmed")
        self.assertEqual(outcome_for(Action.STOP, self.app, stopped, self.app), "confirmed")
        self.assertEqual(outcome_for(Action.UPDATE, self.app, updated, self.app), "confirmed")
        self.assertIsNone(
            outcome_for(Action.UPDATE, self.app, replace(self.app, last_check="T2"), self.app)
        )
        err = replace(self.app, last_error="setup failed")
        self.assertEqual(outcome_for(Action.UPDATE, self.app, err, self.app), "failed")

    async def test_no_conflicting_operation_while_waiting(self):
        signal = asyncio.Event()
        updated = replace(self.app, pid=200)
        self.client.status.side_effect = [
            self.snapshot,
            replace(self.snapshot, apps={"worker": updated}),
        ]
        reports = []

        async def report(phase):
            reports.append(phase)
            signal.set()

        token = self.operations.issue(1, "test", "worker", Action.RESTART)
        first = asyncio.create_task(
            self.operations.execute(self.operations.consume(1, token), report)
        )
        await signal.wait()
        token = self.operations.issue(2, "test", "worker", Action.STOP)
        with self.assertRaisesRegex(SntError, "already running"):
            await self.operations.execute(self.operations.consume(2, token), report)
        await first
        self.assertEqual(reports, ["queued", "confirmed"])
        self.client.submit.assert_awaited_once()

    async def test_command_timeout_is_uncertain_and_never_replayed(self):
        self.client.submit.side_effect = TransportError("Command timed out")
        token = self.operations.issue(1, "test", "worker", Action.RESTART)
        report = AsyncMock()
        await self.operations.execute(self.operations.consume(1, token), report)
        report.assert_awaited_once_with("unconfirmed")
        self.client.submit.assert_awaited_once()

    async def test_observation_timeout_is_uncertain(self):
        token = self.operations.issue(1, "test", "worker", Action.UPDATE)
        report = AsyncMock()
        with patch.object(self.operations.settings, "operation_timeout", 0.01):
            await self.operations.execute(self.operations.consume(1, token), report)
        self.assertEqual(
            [call.args[0] for call in report.await_args_list], ["queued", "unconfirmed"]
        )
