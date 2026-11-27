import json
import platform
import sys
import unittest
from unittest.mock import AsyncMock

from smolbot.core.metrics import LINUX_PROBE, LocalMetrics, SSHMetrics, resource_metrics
from smolbot.transports.base import Result, TransportError
from smolbot.transports.local import LocalTransport


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_measured_local_metrics(self):
        value = await LocalMetrics().collect()
        self.assertTrue(value["hostname"])
        self.assertGreater(value["cpu_count"], 0)
        self.assertGreater(value["memory"]["total"], 0)
        self.assertGreater(value["uptime_seconds"], 0)
        self.assertIn("disk", value)
        self.assertIsNone(resource_metrics({})["cpu"])

    async def test_remote_probe_is_fixed_and_decodes_metrics(self):
        value = {"cpu_percent": 21.0, "memory": {"percent": 55}, "disk": None}
        transport = AsyncMock()
        transport.run.return_value = Result(0, json.dumps(value), "")
        probe = SSHMetrics(transport)
        self.assertEqual(await probe.collect(), value)
        transport.run.assert_awaited_once_with(("python3", "-I", "-c", LINUX_PROBE), 16384)

    async def test_remote_probe_runs_without_os_release_api(self):
        setup = (
            "import os, platform\n"
            "del platform.freedesktop_os_release\n"
            "if not hasattr(os, 'getloadavg'): os.getloadavg = lambda: (0, 0, 0)\n"
        )
        result = await LocalTransport(10).run((sys.executable, "-I", "-c", setup + LINUX_PROBE))
        self.assertEqual(result.code, 0, result.error)
        metrics = json.loads(result.output)
        self.assertEqual(metrics["os"], platform.system())
        self.assertGreater(metrics["disk"]["total"], 0)

    async def test_bad_remote_response_is_sanitized(self):
        transport = AsyncMock()
        transport.run.return_value = Result(0, "secret response", "")
        with self.assertRaisesRegex(TransportError, "^Malformed system metrics$"):
            await SSHMetrics(transport).collect()
