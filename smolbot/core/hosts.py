from dataclasses import dataclass

from smolbot.config import Host
from smolbot.core.metrics import LocalMetrics, SSHMetrics
from smolbot.core.snt import SntClient
from smolbot.transports.local import LocalTransport
from smolbot.transports.ssh import SSHTransport


@dataclass(frozen=True)
class Connection:
    host: Host
    client: SntClient
    metrics: LocalMetrics | SSHMetrics


def connect(settings):
    connections = {}
    for host in settings.hosts:
        if host.transport == "local":
            transport = LocalTransport(settings.command_timeout)
            metrics = LocalMetrics()
        else:
            transport = SSHTransport(host, settings.command_timeout)
            metrics = SSHMetrics(transport)
        connections[host.id] = Connection(
            host,
            SntClient(host, transport, settings.redact_values, settings.protected_apps),
            metrics,
        )
    return connections
