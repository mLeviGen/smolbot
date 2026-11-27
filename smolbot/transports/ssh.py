import os
import shlex

from smolbot.transports.base import TransportError, execute


class SSHTransport:
    def __init__(self, host, timeout):
        self.host = host
        self.timeout = timeout

    def command(self, args):
        host = self.host
        known_hosts = host.known_hosts.replace("\\", "/").replace('"', '\\"').replace("%", "%%")
        return (
            "ssh",
            "-F",
            os.devnull,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            f'UserKnownHostsFile="{known_hosts}"',
            "-o",
            f"GlobalKnownHostsFile={os.devnull}",
            "-o",
            f"ConnectTimeout={host.connect_timeout}",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-i",
            host.identity_file,
            "-p",
            str(host.port),
            "-l",
            host.username,
            "--",
            host.address,
            "exec " + shlex.join(args),
        )

    async def run(self, args, limit=2_000_000):
        result = await execute(self.command(args), self.timeout, limit)
        if result.code == 255:
            error = result.error.lower()
            if (
                "host key verification failed" in error
                or "host identification has changed" in error
            ):
                raise TransportError("SSH host key verification failed")
            if "permission denied" in error:
                raise TransportError("SSH authentication failed")
            if "timed out" in error:
                raise TransportError("SSH connection timed out")
            raise TransportError("SSH connection failed")
        return result
