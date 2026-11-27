from datetime import UTC, datetime


def display(value):
    if value is None or not isinstance(value, (int, float, str)):
        return "N/A"
    return str(value)[:1000]


def timestamp(value):
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "N/A"


def duration(value):
    if type(value) not in (int, float) or not 0 <= value < 10**12:
        return "N/A"
    total = int(value)
    return f"{total // 86400}d {total // 3600 % 24:02}h {total // 60 % 60:02}m"


def percent(value):
    return f"{value:.1f}%" if type(value) in (int, float) and 0 <= value <= 100 else "N/A"


def gib(value):
    return f"{value / 2**30:.2f} GiB" if type(value) is int and 0 <= value < 2**70 else "N/A"


def availability(host, view):
    if not host.enabled:
        return "Monitoring disabled"
    if not view:
        return "Awaiting first poll"
    if view.get("stale") and view.get("online"):
        return "Last known state"
    if not view.get("online"):
        return "Unavailable"
    if not view.get("status_fresh"):
        return "Online · SNT status unavailable"
    return "Online"


def system(metrics):
    metrics = metrics or {}
    memory = metrics.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    disk = metrics.get("disk")
    disk = disk if isinstance(disk, dict) else {}
    load = metrics.get("load_avg")
    return "\n".join(
        [
            f"Hostname: {display(metrics.get('hostname'))}",
            f"OS: {display(metrics.get('os'))}",
            f"Architecture: {display(metrics.get('arch'))}",
            f"Logical CPUs: {display(metrics.get('cpu_count'))}",
            f"CPU: {percent(metrics.get('cpu_percent'))}",
            (
                f"RAM: {gib(memory.get('used'))} used / {gib(memory.get('total'))} · "
                f"{gib(memory.get('available'))} available · {percent(memory.get('percent'))}"
            ),
            (
                f"Disk: {gib(disk.get('used'))} used / {gib(disk.get('total'))} · "
                f"{gib(disk.get('free'))} free · {percent(disk.get('percent'))}"
            ),
            f"System uptime: {duration(metrics.get('uptime_seconds'))}",
            "Load average: "
            + (" / ".join(map(display, load[:3])) if isinstance(load, (list, tuple)) else "N/A"),
        ]
    )


def application(app):
    return "\n".join(
        [
            f"Status: {display(app.get('status')).capitalize()}",
            f"Enabled: {'Yes' if app.get('enabled') else 'No'}",
            f"PID: {display(app.get('pid'))}",
            f"Uptime: {duration(app.get('uptime_seconds'))}",
            f"Restarts: {display(app.get('restarts'))}",
            f"Running commit: {display(app.get('commit'))[:64]}",
            f"Checkout: {display(app.get('checkout'))[:64]}",
            f"Last check: {display(app.get('last_check'))}",
            f"Last error: {display(app.get('last_error'))[:350]}",
            f"Restart policy: {display(app.get('restart_policy'))}",
            f"Check interval (s): {display(app.get('check_interval'))}",
        ]
    )
