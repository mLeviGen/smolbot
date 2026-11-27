import os
import sys
import tempfile
import unittest
from pathlib import Path

from smolbot.core.snt import parse_status
from smolbot.transports.local import LocalTransport

SOURCE = os.environ.get("SMOLBOT_TEST_SNT_SOURCE")


@unittest.skipUnless(SOURCE, "Set SMOLBOT_TEST_SNT_SOURCE to run the real CLI smoke")
class RealSntSmoke(unittest.IsolatedAsyncioTestCase):
    async def test_status_and_disabled_app_in_isolated_home(self):
        transport = LocalTransport(20)
        with tempfile.TemporaryDirectory() as home:
            command = (sys.executable, str(Path(SOURCE) / "snt.py"), "--home", home)
            initial = await transport.run((*command, "status", "--json"))
            self.assertEqual(initial.code, 0)
            snapshot = parse_status(initial.output)
            self.assertEqual(snapshot.version, "2.1.0")
            self.assertFalse(snapshot.daemon_running)
            self.assertEqual(snapshot.apps, {})
            add = await transport.run(
                (
                    *command,
                    "add",
                    "https://github.com/example/isolated-test",
                    "test-worker",
                    "--no-clone",
                    "-c",
                    "python main.py",
                )
            )
            self.assertEqual(add.code, 0)
            stopped = await transport.run((*command, "status", "--json"))
            self.assertEqual(parse_status(stopped.output).apps["test-worker"].status, "stopped")
            stop = await transport.run((*command, "stop", "test-worker"))
            self.assertEqual(stop.code, 0)
            disabled = await transport.run((*command, "status", "--json"))
            self.assertEqual(parse_status(disabled.output).apps["test-worker"].status, "disabled")
