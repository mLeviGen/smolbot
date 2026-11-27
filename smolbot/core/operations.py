import asyncio
import secrets
import time
from dataclasses import dataclass

from smolbot.core.snt import OBSERVATION_ERRORS, Action, SntError
from smolbot.transports.base import TransportError


@dataclass(frozen=True)
class Confirmation:
    user: int
    host: str
    app: str
    action: Action
    expires: float


def outcome_for(action, before, after, previous):
    if action == Action.STOP and not after.enabled and after.status == "disabled":
        return "confirmed"
    if after.last_error and after.last_error != before.last_error:
        return "failed"
    if action == Action.START and after.enabled and after.status == "running":
        return "confirmed"
    if (
        action == Action.RESTART
        and after.status == "running"
        and (
            (after.pid is not None and before.pid is not None and after.pid != before.pid)
            or (after.started_at and before.started_at and after.started_at != before.started_at)
        )
    ):
        return "confirmed"
    if (
        action == Action.UPDATE
        and after.status == "running"
        and before.commit
        and after.commit
        and after.commit != before.commit
        and after.last_check != before.last_check
    ):
        return "confirmed"
    if action == Action.RESTART and previous.status != "running" and after.status == "running":
        if before.status == "running":
            return "confirmed"
    return None


class Operations:
    def __init__(self, connections, settings, storage, *, clock=time.monotonic):
        self.connections = connections
        self.settings = settings
        self.storage = storage
        self.clock = clock
        self.confirmations = {}
        self.running = {}
        self.host_locks = {host: asyncio.Lock() for host in connections}

    def client(self, user, host):
        connection = self.connections.get(host)
        if user not in self.settings.users or connection is None or not connection.host.enabled:
            raise SntError("Operation unavailable")
        return connection.client

    def issue(self, user, host, app, action):
        self.client(user, host).validate_action(action, app)
        now = self.clock()
        self.confirmations = {
            token: item
            for token, item in self.confirmations.items()
            if item.expires > now and item.user != user
        }
        token = secrets.token_urlsafe(16)
        self.confirmations[token] = Confirmation(user, host, app, action, now + 60)
        return token

    def consume(self, user, token):
        item = self.confirmations.get(token)
        if item is None or item.user != user or item.expires <= self.clock():
            raise SntError("Confirmation expired or already used")
        del self.confirmations[token]
        return item

    def intentional(self, host):
        return {
            app
            for (key, app), action in self.running.items()
            if key == host and action in (Action.RESTART, Action.UPDATE)
        }

    async def execute(self, item, report):
        client = self.client(item.user, item.host)
        key = (item.host, item.app)
        if key in self.running or len(self.running) >= self.settings.concurrent_hosts:
            raise SntError("An operation is already running or the operation limit was reached")
        self.running[key] = item.action
        try:
            operation = await self.storage.operation(item.user, *key, item.action.value)
            async with self.host_locks[item.host]:
                try:
                    snapshot = await client.status()
                    before = client.target(snapshot, item.app)
                except OBSERVATION_ERRORS:
                    await self.storage.outcome(operation, "failed")
                    raise
                try:
                    phase = await client.submit(item.action, item.app, snapshot)
                except TransportError:
                    await self.storage.outcome(operation, "unconfirmed")
                    await report("unconfirmed")
                    return
                except SntError:
                    await self.storage.outcome(operation, "failed")
                    raise
            await self.storage.outcome(operation, phase)
            await report(phase)
            try:
                async with asyncio.timeout(self.settings.operation_timeout):
                    previous = before
                    while True:
                        await asyncio.sleep(2)
                        try:
                            after = client.target(await client.status(), item.app)
                        except OBSERVATION_ERRORS:
                            continue
                        result = outcome_for(item.action, before, after, previous)
                        previous = after
                        if result:
                            break
            except TimeoutError:
                result = "unconfirmed"
            await self.storage.outcome(operation, result)
            await report(result)
        finally:
            self.running.pop(key, None)
