import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field

from smolbot.core.metrics import resource_metrics
from smolbot.core.snt import OBSERVATION_ERRORS, Snapshot

log = logging.getLogger(__name__)


@dataclass
class Health:
    failures: int = 0
    online: bool | None = None
    snapshot: Snapshot | None = None
    running_commits: dict[str, str] = field(default_factory=dict)
    levels: dict = field(default_factory=dict)
    candidates: dict = field(default_factory=dict)

    def observe(self, snapshot, metrics, settings, intentional=()):
        events = []

        def event(category, code, message):
            events.append((category, code, message))

        if snapshot is None and metrics is None:
            self.candidates.clear()
            self.failures += 1
            if self.failures == settings.offline_samples and self.online is not False:
                self.online = False
                event("infrastructure", "offline", "Host is unreachable")
            return events
        self.failures = 0
        if self.online is False:
            event("infrastructure", "online", "Host is reachable again")
        self.online = True
        previous = self.snapshot
        if snapshot is not None:
            self.snapshot = snapshot
            last_commits = self.running_commits
            self.running_commits = {
                name: app.commit or last_commits[name]
                for name, app in snapshot.apps.items()
                if app.enabled and (app.commit or name in last_commits)
            }
        if snapshot is not None and previous is not None:
            if previous.daemon_running != snapshot.daemon_running:
                code = "daemon-up" if snapshot.daemon_running else "daemon-down"
                message = (
                    "SNT daemon recovered" if snapshot.daemon_running else "SNT daemon stopped"
                )
                event("infrastructure", code, message)
            same_daemon = (
                previous.daemon_running
                and snapshot.daemon_running
                and (previous.daemon_pid, previous.daemon_started_at)
                == (snapshot.daemon_pid, snapshot.daemon_started_at)
            )
            for name, app in snapshot.apps.items():
                old = previous.apps.get(name)
                if old is None or not app.enabled or app.status == "disabled":
                    continue

                def app_event(code, message, app_name=name):
                    event("applications", f"{app_name}:{code}", f"{app_name}: {message}")

                if (
                    old.status == "running"
                    and app.status in {"waiting", "stopped"}
                    and snapshot.daemon_running
                    and name not in intentional
                ):
                    app_event("down", f"process left running state ({app.status})")
                if (
                    old.enabled
                    and old.status in {"waiting", "stopped"}
                    and app.status == "running"
                    and name not in intentional
                ):
                    app_event("up", "process recovered")
                if (
                    same_daemon
                    and old.restarts is not None
                    and app.restarts is not None
                    and app.restarts > old.restarts
                ):
                    app_event("restarts", f"restart counter increased to {app.restarts}")
                if app.last_error and old.last_error != app.last_error:
                    app_event("error", f"new SNT error: {app.last_error[:300]}")
                if name in last_commits and app.commit and last_commits[name] != app.commit:
                    app_event("commit", f"running commit changed to {app.commit[:12]}")
        for metric, value in resource_metrics(metrics or {}).items():
            if type(value) not in (int, float) or not 0 <= value <= 100:
                self.candidates.pop(metric, None)
                continue
            warning, critical = settings.thresholds[metric]
            level = "critical" if value >= critical else "warning" if value >= warning else "normal"
            before = self.levels.get(metric, "normal")
            if level == before:
                self.candidates.pop(metric, None)
                continue
            candidate, count = self.candidates.get(metric, (None, 0))
            # Alternating critical/warning readings still indicate sustained high usage.
            if before == "normal" and candidate in {"warning", "critical"} and level != "normal":
                count += 1
                level = "warning" if candidate == "warning" else level
            else:
                count = count + 1 if level == candidate else 1
            self.candidates[metric] = (level, count)
            if count >= settings.resource_samples:
                self.levels[metric] = level
                self.candidates.pop(metric, None)
                description = "back to normal" if level == "normal" else level
                event(
                    "resources", f"{metric}:{level}", f"{metric.upper()} {description} ({value}%)"
                )
        return events


class Monitor:
    def __init__(self, connections, settings, storage, operations=None):
        self.connections = connections
        self.settings = settings
        self.storage = storage
        self.operations = operations
        self.gate = asyncio.Semaphore(settings.concurrent_hosts)
        self.locks = {key: asyncio.Lock() for key in connections}
        self.health = {key: Health() for key in connections}
        self.views = {}

    async def restore(self):
        self.views = {
            key: {**view, "stale": True}
            for key, view in (await self.storage.snapshots()).items()
            if key in self.connections and self.connections[key].host.enabled
        }

    @staticmethod
    async def _collect(function):
        try:
            return await function(), None
        except OBSERVATION_ERRORS as error:
            return None, str(error)
        except Exception as error:
            log.warning("Host probe failed (%s)", type(error).__name__)
            return None, "Host probe failed"

    async def refresh(self, key, *, manual=False):
        connection = self.connections[key]
        if not connection.host.enabled:
            return
        lock = self.locks[key]
        if manual and lock.locked():
            return
        async with lock, self.gate:
            # Run sequential commands per host to keep SSH process count bounded by the gate.
            snapshot, snt_error = await self._collect(connection.client.status)
            metrics, metrics_error = await self._collect(connection.metrics.collect)
            now = time.time()
            health = self.health[key]
            # Manual refreshes update views without accelerating scheduled alert debouncing.
            intentional = self.operations.intentional(key) if self.operations else ()
            events = [] if manual else health.observe(snapshot, metrics, self.settings, intentional)
            old = self.views.get(key, {})
            reachable = snapshot is not None or metrics is not None
            view = {
                "online": reachable,
                "stale": not reachable,
                "status": asdict(snapshot) if snapshot is not None else old.get("status"),
                "status_fresh": snapshot is not None,
                "metrics": metrics,
                "snt_error": snt_error,
                "metrics_error": metrics_error,
                "last_success": now if reachable else old.get("last_success"),
                "last_status": now if snapshot is not None else old.get("last_status"),
                "resource_levels": dict(health.levels),
            }
            await self.storage.record(
                key,
                view,
                events,
                self.settings.users if self.settings.alerts_enabled else (),
                self.settings.cooldown,
                now,
            )
            self.views[key] = view

    async def cycle(self):
        async with asyncio.TaskGroup() as tasks:
            for key, connection in self.connections.items():
                if connection.host.enabled:
                    tasks.create_task(self.refresh(key))

    async def run(self):
        last_prune = None
        while True:
            try:
                await self.cycle()
                now = time.time()
                if last_prune is None or now - last_prune >= 3600:
                    await self.storage.prune(now, self.settings.retention_days)
                    last_prune = now
            except Exception as error:
                log.warning("Monitoring cycle failed (%s)", type(error).__name__)
            await asyncio.sleep(self.settings.poll_interval)
