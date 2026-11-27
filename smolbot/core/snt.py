import json
from dataclasses import dataclass
from enum import StrEnum

from smolbot.config import APP_NAME
from smolbot.redaction import redact
from smolbot.transports.base import TransportError


class SntError(Exception):
    pass


class Action(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    UPDATE = "update"


LOG_LINES = (20, 50, 100, 200)


@dataclass(frozen=True)
class App:
    name: str
    status: str
    enabled: bool
    pid: int | None = None
    started_at: str | None = None
    uptime_seconds: int | None = None
    restarts: int | None = None
    commit: str | None = None
    checkout: str | None = None
    last_check: str | None = None
    last_error: str | None = None
    restart_policy: str | None = None
    check_interval: int | None = None


@dataclass(frozen=True)
class Snapshot:
    version: str | None
    daemon_running: bool
    daemon_pid: int | None
    daemon_started_at: str | None
    apps: dict[str, App]


def parse_status(output, secrets=()):
    def optional(data, key, kind):
        value = data.get(key)
        if type(value) is kind:
            return redact(value, secrets)[:1000] if kind is str else value
        return None

    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            raise TypeError
        if type(data.get("schema")) is not int or data["schema"] != 1:
            raise SntError("Unsupported SNT status schema")
        daemon = data["daemon"]
        if type(daemon["running"]) is not bool or not isinstance(data["apps"], list):
            raise ValueError
        apps = {}
        for entry in data["apps"]:
            name, status, enabled = entry["name"], entry["status"], entry["enabled"]
            if not APP_NAME.fullmatch(name) or name in apps or type(enabled) is not bool:
                raise ValueError
            if status not in ("running", "waiting", "stopped", "disabled"):
                status = "unknown"
            apps[name] = App(
                name,
                status,
                enabled,
                **{
                    key: optional(entry, key, int)
                    for key in ("pid", "uptime_seconds", "restarts", "check_interval")
                },
                **{
                    key: optional(entry, key, str)
                    for key in (
                        "started_at",
                        "commit",
                        "checkout",
                        "last_check",
                        "last_error",
                        "restart_policy",
                    )
                },
            )
        return Snapshot(
            optional(data, "smolnugget", str),
            daemon["running"],
            optional(daemon, "pid", int),
            optional(daemon, "started_at", str),
            apps,
        )
    except (ValueError, KeyError, TypeError, RecursionError):
        raise SntError("Malformed SNT status response") from None


class SntClient:
    def __init__(self, host, transport, secrets, protected):
        self.transport = transport
        self.base = (host.snt_path,)
        if host.snt_home:
            self.base += ("--home", host.snt_home)
        self.secrets = secrets
        self.protected = protected | {"smolbot"}

    async def status(self):
        result = await self.transport.run((*self.base, "status", "--json"))
        if result.code or result.truncated:
            raise SntError("SNT status failed or exceeded the output limit")
        return parse_status(result.output, self.secrets)

    @staticmethod
    def target(snapshot, name):
        if not isinstance(name, str) or not APP_NAME.fullmatch(name) or name not in snapshot.apps:
            raise SntError("Application no longer exists on this host")
        return snapshot.apps[name]

    async def logs(self, name, count):
        if count not in LOG_LINES:
            raise SntError("Invalid log line count")
        self.target(await self.status(), name)
        result = await self.transport.run((*self.base, "logs", name, "-n", str(count)), 131072)
        if result.code:
            raise SntError("SNT could not read the log")
        if result.truncated:
            # The cutoff can split a sensitive value before it can be matched.
            raise SntError("Log output exceeds the size limit. Choose fewer lines.")
        return redact(result.output, self.secrets)

    def validate_action(self, action, name):
        if not isinstance(action, Action):
            raise SntError("Unknown operation")
        if not isinstance(name, str) or not APP_NAME.fullmatch(name):
            raise SntError("Invalid application name")
        if name.casefold() in self.protected:
            raise SntError("This application is protected")

    async def submit(self, action, name, snapshot):
        self.validate_action(action, name)
        app = self.target(snapshot, name)
        if action == Action.START and app.status == "running" and app.enabled:
            raise SntError("Application is already running")
        if action == Action.STOP and app.status == "disabled" and not app.enabled:
            raise SntError("Application is already disabled")
        result = await self.transport.run((*self.base, action.value, name), 8192)
        if result.code:
            raise SntError(f"SNT rejected the request (exit {result.code})")
        if "(request queued)" in result.output:
            return "queued"
        if action in (Action.RESTART, Action.UPDATE):
            return "in_progress"
        return "accepted"


OBSERVATION_ERRORS = (TransportError, SntError)
