import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import SendMessage

from smolbot.config import Host
from smolbot.core.alerts import deliver
from smolbot.core.monitoring import Health, Monitor
from smolbot.core.snt import App, Snapshot
from smolbot.storage import Storage
from smolbot.transports.base import TransportError


def settings(**overrides):
    defaults = {
        "offline_samples": 3,
        "resource_samples": 3,
        "concurrent_hosts": 2,
        "thresholds": {"cpu": (85, 95), "ram": (80, 90), "disk": (80, 90)},
        "users": frozenset({1}),
        "alerts_enabled": True,
        "cooldown": 300,
        "operation_timeout": 5,
    }
    return SimpleNamespace(**(defaults | overrides))


def snapshot(app=None, daemon=True, pid=100):
    return Snapshot("2.1.0", daemon, pid, None, {app.name: app} if app else {})


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.health = Health()
        self.config = settings()
        self.worker = App("worker", "running", True, commit="first", restarts=4)

    def observe(self, state=None, metrics=None):
        return {key for _, key, _ in self.health.observe(state, metrics, self.config)}

    def test_baseline_and_offline_recovery(self):
        self.assertEqual(self.observe(snapshot(self.worker)), set())
        self.assertEqual(self.observe(), set())
        self.assertEqual(self.observe(), set())
        self.assertEqual(self.observe(snapshot(self.worker)), set())
        self.assertEqual(self.observe(), set())
        self.assertEqual(self.observe(), set())
        self.assertEqual(self.observe(), {"offline"})
        self.assertEqual(self.observe(), set())
        self.assertEqual(self.observe(metrics={}), {"online"})

    def test_app_fall_recovery_commit_change_and_error(self):
        self.observe(snapshot(self.worker))
        waiting = replace(self.worker, status="waiting", restarts=5, last_error="setup failed")
        self.assertEqual(
            self.observe(snapshot(waiting)), {"worker:down", "worker:restarts", "worker:error"}
        )
        self.assertEqual(self.observe(snapshot(waiting)), set())
        recovered = replace(self.worker, restarts=5, commit="next")
        self.assertEqual(self.observe(snapshot(recovered)), {"worker:up", "worker:commit"})

    def test_daemon_and_counter_reset(self):
        self.observe(snapshot(self.worker))
        self.assertEqual(self.observe(snapshot(daemon=False)), {"daemon-down"})
        self.assertEqual(
            self.observe(snapshot(replace(self.worker, restarts=1), pid=101)), {"daemon-up"}
        )
        self.assertEqual(
            self.observe(snapshot(replace(self.worker, restarts=2), pid=101)), {"worker:restarts"}
        )

    def test_disabled_app_has_no_outage(self):
        self.observe(snapshot(self.worker))
        self.assertEqual(
            self.observe(snapshot(replace(self.worker, status="disabled", enabled=False))), set()
        )

    def test_resources_require_consecutive_samples_and_recover(self):
        for cpu in (96, 5, 96, 90):
            self.assertEqual(self.observe(metrics={"cpu_percent": cpu}), set())
        self.assertEqual(self.observe(metrics={"cpu_percent": 96}), {"cpu:warning"})
        for _ in range(2):
            self.assertEqual(self.observe(metrics={"cpu_percent": 96}), set())
        self.assertEqual(self.observe(metrics={"cpu_percent": 96}), {"cpu:critical"})
        for _ in range(2):
            self.assertEqual(self.observe(metrics={"cpu_percent": 15}), set())
        self.assertEqual(self.observe(metrics={"cpu_percent": 15}), {"cpu:normal"})


class StorageAndMonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.db"
        self.storage = Storage(self.path)
        await self.storage.open()

    async def test_restart_persists_preferences_events_and_interrupts_operations(self):
        await self.storage.set_preferences(1, mute=10, category="resources")
        await self.storage.operation(1, "host", "worker", "restart")
        await self.storage.record(
            "host", {"online": True}, [("applications", "up", "Up")], {1}, 300, 20
        )
        again = Storage(self.path)
        await again.open()
        self.assertEqual(await again.preferences(1), (10, {"resources"}))
        self.assertEqual(await again.snapshots(), {"host": {"online": True}})
        self.assertEqual(len(await again.pending(21)), 1)
        operation = await again.call(
            lambda db: db.execute("SELECT outcome FROM operations").fetchone()
        )
        self.assertEqual(operation[0], "interrupted")

    async def test_event_dedup_cooldown_and_recovery(self):
        for now, code in (
            (500, "offline"),
            (500, "offline"),
            (510, "offline"),
            (520, "online"),
            (801, "offline"),
        ):
            await self.storage.record("host", {}, [("infrastructure", code, code)], {1}, 300, now)
        self.assertEqual(
            [row[-1] for row in await self.storage.pending(805)], ["offline", "online", "offline"]
        )

    async def test_parameterized_sql_and_pruning(self):
        host = "x'); DROP TABLE users; --"
        await self.storage.record(host, {"online": True}, [], {1}, 300, 1)
        self.assertIn(host, await self.storage.snapshots())
        await self.storage.prune(172801, 1)
        self.assertEqual(await self.storage.snapshots(), {})
        self.assertEqual(await self.storage.preferences(1), (0, set()))

    async def test_alert_api_failure_is_retried_and_allowlist_rechecked(self):
        await self.storage.record(
            "host", {}, [("infrastructure", "offline", "Offline")], {1, 2}, 300, 100
        )
        bot = AsyncMock()
        method = SendMessage(chat_id=1, text="message")
        bot.send_message.side_effect = TelegramNetworkError(method, "network")
        await deliver(bot, self.storage, settings(), now=101)
        self.assertEqual(len(await self.storage.pending(101)), 1)
        bot.send_message.side_effect = TelegramForbiddenError(method, "blocked")
        await deliver(bot, self.storage, settings(), now=102)
        self.assertEqual(await self.storage.pending(103), [])

    async def test_one_failed_host_does_not_block_others_and_concurrency_is_bounded(self):
        active = peak = 0
        parallel = asyncio.Event()
        apps = {f"worker{index}": App(f"worker{index}", "running", True) for index in range(5)}

        async def collect():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                if active == 2:
                    parallel.set()
                await asyncio.wait_for(parallel.wait(), 3)
                await asyncio.sleep(0)
                return replace(snapshot(), apps=apps)
            finally:
                active -= 1

        hosts = {}
        for index in range(10):
            key = f"host{index}"
            client = AsyncMock()
            client.status.side_effect = (
                collect if index else TransportError("SSH connection failed")
            )
            probe = AsyncMock()
            if index:
                probe.collect.return_value = {"cpu_percent": 2}
            else:
                probe.collect.side_effect = TransportError("SSH connection failed")
            hosts[key] = SimpleNamespace(
                host=Host(key, "Host", "local", "snt"), client=client, metrics=probe
            )
        monitor = Monitor(hosts, settings(), self.storage)
        await monitor.cycle()
        self.assertFalse(monitor.views["host0"]["online"])
        hosts["host0"].client.status.side_effect = collect
        await monitor.cycle()
        self.assertEqual(sum(len(view["status"]["apps"]) for view in monitor.views.values()), 50)
        self.assertEqual(peak, 2)
        self.assertTrue(monitor.views["host9"]["online"])
        self.assertEqual(len(await self.storage.pending(0)), 0)

    async def test_metric_success_distinguishes_snt_failure_from_unreachable_host(self):
        client = AsyncMock()
        client.status.side_effect = TransportError("SNT command unavailable")
        probe = AsyncMock()
        probe.collect.return_value = {"cpu_percent": 2}
        monitor = Monitor(
            {
                "host": SimpleNamespace(
                    host=Host("host", "Host", "local", "snt"),
                    client=client,
                    metrics=probe,
                )
            },
            settings(),
            self.storage,
        )
        await monitor.cycle()
        view = monitor.views["host"]
        self.assertTrue(view["online"])
        self.assertFalse(view["status_fresh"])
        self.assertEqual(view["snt_error"], "SNT command unavailable")
