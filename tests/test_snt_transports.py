import asyncio
import json
import shlex
import sys
import unittest
from unittest.mock import AsyncMock, patch

from smolbot.config import Host
from smolbot.core.snt import Action, SntClient, SntError, parse_status
from smolbot.redaction import redact
from smolbot.transports.base import Result, TransportError
from smolbot.transports.local import LocalTransport
from smolbot.transports.ssh import SSHTransport


def sample(*apps, running=True):
    return json.dumps(
        {
            "schema": 1,
            "smolnugget": "2.1.0",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "daemon": {"running": running, "pid": 100 if running else None, "started_at": None},
            "apps": apps,
            "future_field": ["ignored"],
        }
    )


def entry(name, status="running", **extra):
    return {"name": name, "status": status, "enabled": status != "disabled", **extra}


class SntTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = AsyncMock()
        self.transport.run.return_value = Result(0, sample(entry("worker")), "")
        self.client = SntClient(
            Host("host", "Host", "local", "snt"), self.transport, ("exact-secret",), frozenset()
        )

    def test_schema_variants_and_empty(self):
        states = ("running", "waiting", "stopped", "disabled")
        snapshot = parse_status(sample(*(entry(status, status, optional=123) for status in states)))
        self.assertEqual(tuple(app.status for app in snapshot.apps.values()), states)
        self.assertIsNone(snapshot.apps["waiting"].pid)
        self.assertFalse(parse_status(sample(running=False)).daemon_running)
        self.assertEqual(parse_status(sample()).apps, {})
        self.assertEqual(parse_status(sample(entry("new", "future"))).apps["new"].status, "unknown")

    def test_bad_schema_and_invalid_json(self):
        with self.assertRaisesRegex(SntError, "Unsupported"):
            parse_status('{"schema": 2}')
        for value in (
            "broken-secret",
            "[]",
            sample(entry("../other")),
            sample(entry("a"), entry("a")),
        ):
            with self.subTest(value=value), self.assertRaisesRegex(SntError, "Malformed"):
                parse_status(value)

    async def test_queue_only(self):
        snapshot = await self.client.status()
        self.transport.run.return_value = Result(0, "worker: restarting (request queued)\n", "")
        self.assertEqual(await self.client.submit(Action.RESTART, "worker", snapshot), "queued")
        self.transport.run.assert_awaited_with(("snt", "restart", "worker"), 8192)

    async def test_failed_control_hides_output(self):
        snapshot = await self.client.status()
        self.transport.run.return_value = Result(1, "", "private command details")
        with self.assertRaisesRegex(SntError, "^SNT rejected the request"):
            await self.client.submit(Action.STOP, "worker", snapshot)

    async def test_validated_operation_and_target(self):
        snapshot = await self.client.status()
        for action, target in (
            (Action.STOP, "worker; echo bad"),
            (Action.STOP, "../worker"),
            (Action.STOP, "worker2"),
            (Action.STOP, "SMOLBOT"),
            ("run", "worker"),
        ):
            with self.subTest(action=action, target=target), self.assertRaises(SntError):
                await self.client.submit(action, target, snapshot)
        self.assertEqual(self.transport.run.await_count, 1)

    async def test_log_command_and_redaction(self):
        self.transport.run.side_effect = [
            Result(0, sample(entry("worker")), ""),
            Result(0, "hello exact-secret password=other", ""),
        ]
        self.assertEqual(await self.client.logs("worker", 50), "hello [REDACTED] [REDACTED]")
        self.transport.run.assert_awaited_with(("snt", "logs", "worker", "-n", "50"), 131072)

    async def test_partial_logs_are_refused(self):
        self.transport.run.side_effect = [
            Result(0, sample(entry("worker")), ""),
            Result(0, "fragment", "", True),
        ]
        with self.assertRaisesRegex(SntError, "size limit"):
            await self.client.logs("worker", 200)

    def test_key_and_authorization_header_are_masked(self):
        value = (
            "Authorization: Bearer sample-value\n-----BEGIN OPENSSH " "PRIVATE KEY-----\npayload"
        )
        self.assertNotIn("sample-value", redact(value, ()))
        self.assertNotIn("payload", redact(value, ()))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ssh = SSHTransport(
            Host(
                "host",
                "Host",
                "ssh",
                "/path with spaces/snt",
                address="server.example",
                username="levi",
                identity_file="/key",
                known_hosts="/known hosts",
            ),
            8,
        )

    def test_ssh_quotes_arguments(self):
        args = ("/path with spaces/snt", "logs", "a; echo $(false) '", "-n", "50")
        command = self.ssh.command(args)
        self.assertEqual(shlex.split(command[-1]), ["exec", *args])
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertIn('UserKnownHostsFile="/known hosts"', command)

    async def test_ssh_errors_sanitized(self):
        for error, expected in (
            ("Permission denied (publickey).", "authentication"),
            ("Host key verification failed.", "verification"),
            ("Connection timed out", "timed out"),
        ):
            with (
                self.subTest(error=error),
                patch(
                    "smolbot.transports.ssh.execute",
                    return_value=Result(255, "", error),
                ),
                self.assertRaisesRegex(TransportError, expected),
            ):
                await self.ssh.run(("/snt", "status", "--json"))

    async def test_real_local_subprocess_limits(self):
        result = await LocalTransport(5).run(
            (sys.executable, "-c", "import sys;print('a'*20000);print('err',file=sys.stderr)"), 512
        )
        self.assertEqual(result.code, 0)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.output), 512)
        self.assertEqual(result.error.strip(), "err")

    async def test_real_local_timeout(self):
        with self.assertRaisesRegex(TransportError, "timed out"):
            await LocalTransport(0.2).run((sys.executable, "-c", "import time;time.sleep(10)"))

    async def test_real_local_cancellation(self):
        task = asyncio.create_task(
            LocalTransport(10).run((sys.executable, "-c", "import time;time.sleep(10)"))
        )
        await asyncio.sleep(0.2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
