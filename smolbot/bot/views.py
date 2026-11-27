import time
from dataclasses import replace

from smolbot import __version__
from smolbot.bot.access import Page
from smolbot.bot.formatters import (
    application,
    availability,
    display,
    percent,
    resource_summary,
    system,
    timestamp,
)
from smolbot.core.alerts import CATEGORIES
from smolbot.core.metrics import resource_metrics
from smolbot.core.snt import LOG_LINES, Action, SntError

COMMANDS = {
    "start": "Open main menu",
    "help": "Show commands",
    "status": "Infrastructure overview",
    "hosts": "List hosts",
    "system": "Open resources dashboard",
    "apps": "Browse applications",
    "logs": "Browse application logs",
    "restart": "Restart an application",
    "startapp": "Start an application",
    "stop": "Stop an application",
    "update": "Check for application updates",
    "alerts": "Alert preferences",
    "settings": "Bot settings",
    "version": "SmolBot version",
}
SHORTCUTS = {
    "logs": "logs",
    "restart": "restart",
    "startapp": "start",
    "stop": "stop",
    "update": "update",
}
PAGE_SIZE = 8
HOME = [[("Home", Page("home"))]]
PHASE = {
    "accepted": "Request accepted. Waiting for an observed result…",
    "queued": "Request queued by SNT. Waiting for an observed result…",
    "in_progress": "SNT consumed the request. Waiting for an observed result…",
    "confirmed": "Expected process state observed.",
    "failed": "SNT reported a new application error.",
    "unconfirmed": "Result could not be confirmed. Check status and logs before retrying.",
}


class Views:
    def __init__(self, monitor, settings, storage):
        self.monitor = monitor
        self.settings = settings
        self.storage = storage

    @staticmethod
    def paginate(items, page):
        last = max(0, (len(items) - 1) // PAGE_SIZE)
        index = min(last, max(0, page.number))
        plus = []
        if index:
            plus.append(("‹ Previous", replace(page, number=index - 1)))
        if index < last:
            plus.append(("Next ›", replace(page, number=index + 1)))
        start = index * PAGE_SIZE
        rows = [[item] for item in items[start : start + PAGE_SIZE]]
        if plus:
            rows.append(plus)
        return rows

    async def render(self, user, page):
        hosts = self.monitor.connections
        views = self.monitor.views
        if page.route == "home":
            current = [views.get(key, {}) for key, conn in hosts.items() if conn.host.enabled]
            online = sum(bool(view.get("online") and not view.get("stale")) for view in current)
            apps = [
                app
                for view in current
                if view.get("status_fresh") and not view.get("stale")
                for app in (view.get("status") or {}).get("apps", {}).values()
            ]
            warnings = sum(
                int(bool(view.get("snt_error") or view.get("metrics_error")))
                + sum(level != "normal" for level in view.get("resource_levels", {}).values())
                for view in current
            ) + sum(
                app["enabled"] and (app["status"] != "running" or bool(app["last_error"]))
                for app in apps
            )
            running = sum(app["status"] == "running" for app in apps)
            refreshed = max((view.get("last_success") or 0 for view in current), default=0)
            return (
                (
                    f"SmolBot · Overview\n\n🖥  {online}/{len(current)} online"
                    f"\n📦  {running}/{len(apps)} applications running\n⚠️  {warnings} warnings"
                    f"\n\nLast successful poll: {timestamp(refreshed)}"
                ),
                [
                    [("Hosts", Page("hosts")), ("Applications", Page("apps"))],
                    [("Resources", Page("resources")), ("Alerts", Page("alerts"))],
                    [("Settings", Page("settings")), ("Refresh", Page("refresh_all"))],
                ],
            )
        if page.route == "resources":
            samples, rows, warnings = [], [], []
            for key, conn in hosts.items():
                if not conn.host.enabled:
                    continue
                view = views.get(key, {})
                metrics = (
                    view.get("metrics") if view.get("online") and not view.get("stale") else None
                )
                available = resource_metrics(metrics or {})
                if metrics:
                    samples.append(metrics)
                    for metric, label in (("cpu", "CPU"), ("ram", "RAM"), ("disk", "Disk")):
                        value = available[metric]
                        rendered = percent(value)
                        if rendered != "N/A" and value >= self.settings.thresholds[metric][0]:
                            warnings.append(f"{conn.host.name} · {label} {rendered}")
                detail = (
                    " / ".join(percent(available[k]) for k in ("cpu", "ram", "disk"))
                    if metrics
                    else "No current metrics"
                )
                rows.append((f"{conn.host.name[:52]} · {detail}", Page("system", key)))
            text = (
                f"SmolBot · Resources\n\n{len(samples)}/{len(rows)} hosts reporting metrics"
                f"\n\n{resource_summary(samples)}"
            )
            if warnings:
                text += "\n\n⚠️  Above threshold\n" + "\n".join(warnings[:4])
                if len(warnings) > 4:
                    text += f"\n+{len(warnings) - 4} more"
            if rows:
                text += "\n\nSelect a host · CPU / RAM / Disk"
            return text, self.paginate(rows, page) + [
                [
                    ("Overview", Page("home")),
                    ("Refresh", Page("refresh_all", number=page.number, value="resources")),
                ],
            ]
        if page.route == "help":
            return "\n".join(f"/{name} — {label}" for name, label in COMMANDS.items()), HOME
        if page.route == "version":
            return f"SmolBot {__version__}", HOME
        if page.route == "hosts":
            rows = [
                (
                    f"{conn.host.name} · {availability(conn.host, views.get(key))}",
                    Page(page.value or "host", key),
                )
                for key, conn in hosts.items()
            ]
            return "Select a host", self.paginate(rows, page) + HOME
        if page.route == "alerts":
            mute, disabled = await self.storage.preferences(user)
            label = f"Muted until {timestamp(mute)}" if mute > time.time() else "Mute for 1 hour"
            rows = [
                [
                    (
                        f"{category.title()} · {'Off' if category in disabled else 'On'}",
                        Page("toggle", value=category),
                    )
                ]
                for category in CATEGORIES
            ]
            rows.append(
                [
                    (
                        label if mute <= time.time() else "Resume alerts",
                        Page("mute", value="0" if mute > time.time() else "3600"),
                    )
                ]
            )
            state = "Enabled" if self.settings.alerts_enabled else "Disabled globally"
            return (
                f"Alerts · {state}\n{label if mute > time.time() else 'Delivery preferences'}",
                rows + HOME,
            )
        if page.route == "settings":
            return (
                (
                    f"Settings\nPoll interval: {self.settings.poll_interval}s\n"
                    f"Event retention: {self.settings.retention_days} days\n"
                    f"Concurrent hosts: {self.settings.concurrent_hosts}"
                ),
                [[("Alerts", Page("alerts"))]] + HOME,
            )
        if page.route == "apps":
            if page.host and page.host not in hosts:
                raise SntError("Host no longer exists")
            rows = []
            for key in (page.host,) if page.host else hosts:
                status = (views.get(key) or {}).get("status") or {}
                for name, app in sorted(status.get("apps", {}).items()):
                    label = (
                        f"{name} · {app['status']}"
                        if page.host
                        else f"{name} · {key} · {app['status']}"
                    )
                    target = (
                        Page("logs", key, name)
                        if page.value == "logs"
                        else (
                            Page("request", key, name, value=page.value)
                            if page.value
                            else Page("app", key, name)
                        )
                    )
                    rows.append((label, target))
            back = [[("Back", Page("host", page.host))]] if page.host else HOME
            return (
                "Applications" if rows else "No application data available",
                self.paginate(rows, page) + back,
            )
        connection = hosts.get(page.host)
        if connection is None:
            raise SntError("Host no longer exists")
        view = views.get(page.host, {})
        status = view.get("status") or {}
        if page.route in {"host", "system"}:
            host = connection.host
            state = availability(host, view)
            if status and (view.get("stale") or not view.get("status_fresh")):
                state += " · showing last known SNT status"
            apps = status.get("apps", {})
            running = sum(app["status"] == "running" for app in apps.values())
            text = (
                f"{host.name} ({host.id})\n{state}\n\n"
                f"SNT: {display(status.get('version'))}\n"
                f"Daemon: {display(status.get('daemon_running'))} · PID {display(status.get('daemon_pid'))}\n"
                f"Daemon started: {display(status.get('daemon_started_at'))}\n"
                f"Applications: {running}/{len(apps)} running\n"
                f"Last successful poll: {timestamp(view.get('last_success'))}\n"
                f"Last SNT status: {timestamp(view.get('last_status'))}"
            )
            for key in ("snt_error", "metrics_error"):
                if view.get(key):
                    text += "\n" + view[key]
            if page.route == "system":
                text += "\n\n" + system(view.get("metrics"))
            return text, [
                [("Apps", Page("apps", page.host)), ("System", Page("system", page.host))],
                [
                    ("Refresh", replace(page, route="refresh", value=page.route)),
                    (
                        "Resources" if page.route == "system" else "Hosts",
                        Page("resources") if page.route == "system" else Page("hosts"),
                    ),
                ],
            ]
        app = status.get("apps", {}).get(page.app)
        if app is None:
            raise SntError("Application unavailable. Refresh the host first.")
        back = [[("Back", Page("app", page.host, page.app))]]
        if page.route == "logs":
            return (
                f"{page.app} · log length",
                [
                    [
                        (str(count), Page("read_log", page.host, page.app, count))
                        for count in LOG_LINES
                    ],
                ]
                + back,
            )
        if page.route != "app":
            raise SntError("This menu is no longer available")
        rows = [
            [
                ("Logs", Page("logs", page.host, page.app)),
                ("Refresh", Page("refresh", page.host, page.app, value="app")),
            ]
        ]
        if connection.host.enabled and page.app.casefold() not in self.settings.protected_apps:
            rows += [
                [
                    (action.value.title(), Page("request", page.host, page.app, value=action.value))
                    for action in group
                ]
                for group in ((Action.START, Action.STOP), (Action.RESTART, Action.UPDATE))
            ]
        stale = (
            "\nShowing last known SNT status"
            if not view.get("status_fresh") or view.get("stale")
            else ""
        )
        return (
            f"{page.app} · {page.host}{stale}\n\n{application(app)}",
            rows + [[("Back", Page("apps", page.host))]],
        )
