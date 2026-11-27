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


def meter(value):
    if percent(value) == "N/A":
        return "N/A"
    filled = round(value / 10)
    return f"{'▰' * filled}{'▱' * (10 - filled)}  {percent(value)}"


def resource_summary(samples):
    cpu_weights = [
        (sample["cpu_count"], sample["cpu_percent"])
        for sample in samples
        if type(sample.get("cpu_count")) is int
        and 0 < sample["cpu_count"] < 2**31
        and percent(sample.get("cpu_percent")) != "N/A"
    ]
    cores = sum(count for count, _ in cpu_weights)
    cpu = sum(count * load for count, load in cpu_weights) / cores if cores else None
    lines = [f"CPU · {cores} cores · {len(cpu_weights)} hosts", meter(cpu)]

    for label, key in (("RAM", "memory"), ("Disk", "disk")):
        pairs = []
        for sample in samples:
            metric = sample.get(key)
            if isinstance(metric, dict):
                used, total = metric.get("used"), metric.get("total")
                if (
                    type(used) is int
                    and type(total) is int
                    and 0 <= used <= total < 2**70
                    and total
                ):
                    pairs.append((used, total))
        used = sum(a for a, _ in pairs)
        capacity = sum(b for _, b in pairs)
        volumes = f" · {gib(used)} / {gib(capacity)}" if capacity else ""
        lines.extend(
            (
                f"\n{label} · {len(pairs)} hosts{volumes}",
                meter(used * 100 / capacity if capacity else None),
            )
        )
    return "\n".join(lines)


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
            "",
            f"CPU  {meter(metrics.get('cpu_percent'))}",
            (
                f"RAM: {gib(memory.get('used'))} used / {gib(memory.get('total'))} · "
                f"{gib(memory.get('available'))} available\n{meter(memory.get('percent'))}"
            ),
            (
                f"Disk: {gib(disk.get('used'))} used / {gib(disk.get('total'))} · "
                f"{gib(disk.get('free'))} free\n{meter(disk.get('percent'))}"
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
