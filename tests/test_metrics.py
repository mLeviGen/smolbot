import json
import unittest
from unittest.mock import AsyncMock

from smolbot.core.metrics import LINUX_PROBE, LocalMetrics, SSHMetrics, resource_metrics
from smolbot.transports.base import Result, TransportError


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

    async def test_bad_remote_response_is_sanitized(self):
        transport = AsyncMock()
        transport.run.return_value = Result(0, "secret response", "")
        with self.assertRaisesRegex(TransportError, "^Malformed system metrics$"):
            await SSHMetrics(transport).collect()
