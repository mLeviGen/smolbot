import asyncio
import json
import os
import platform
import socket
import time
from pathlib import Path

import psutil

from smolbot.transports.base import TransportError

# Fixed source. Remote execution requires only Python's standard library and procfs.
LINUX_PROBE = """
import json
import os
import platform
import shutil
import socket
import time
from pathlib import Path

def optional(function):
    try:
        return function()
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return None

def cpu_ticks():
    fields = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    return sum(fields), fields[3] + fields[4]

def cpu():
    total, idle = cpu_ticks()
    time.sleep(0.25)
    new_total, new_idle = cpu_ticks()
    return round(100 * (1 - (new_idle - idle) / (new_total - total)), 1)

def memory():
    entries = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    total = int(entries['MemTotal'].split()[0]) * 1024
    available = int(entries['MemAvailable'].split()[0]) * 1024
    return dict(total=total, used=total-available, available=available,
                percent=round((total-available)*100/total, 1))

def disk():
    total, used, free = shutil.disk_usage('/')
    return dict(total=total, used=used, free=free, percent=round(used*100/total, 1))

print(json.dumps(dict(hostname=socket.gethostname(),
    os=optional(lambda: platform.freedesktop_os_release().get('PRETTY_NAME')) or platform.system(),
    arch=platform.machine(), cpu_count=os.cpu_count(), cpu_percent=optional(cpu),
    memory=optional(memory), disk=optional(disk), load_avg=optional(os.getloadavg),
    uptime_seconds=optional(lambda: int(float(Path('/proc/uptime').read_text().split()[0]))))))
"""


def optional(function, *args):
    try:
        return function(*args)
    except (OSError, ValueError, NotImplementedError):
        return None


def local_metrics():
    fs = Path.home().anchor
    disk = optional(psutil.disk_usage, fs)
    mem = optional(psutil.virtual_memory)
    boot = optional(psutil.boot_time)
    release = optional(platform.freedesktop_os_release) or {}
    return {
        "hostname": socket.gethostname(),
        "os": release.get("PRETTY_NAME") or platform.platform(terse=True),
        "arch": platform.machine(),
        "cpu_count": psutil.cpu_count(),
        "cpu_percent": optional(psutil.cpu_percent, 0.25),
        "memory": (
            {
                "total": mem.total,
                "used": mem.total - mem.available,
                "available": mem.available,
                "percent": mem.percent,
            }
            if mem
            else None
        ),
        "disk": disk._asdict() if disk else None,
        "uptime_seconds": int(time.time() - boot) if boot is not None else None,
        "load_avg": optional(getattr(os, "getloadavg", lambda: None)),
    }


class LocalMetrics:
    async def collect(self):
        return await asyncio.to_thread(local_metrics)


class SSHMetrics:
    def __init__(self, transport):
        self.transport = transport

    async def collect(self):
        result = await self.transport.run(("python3", "-I", "-c", LINUX_PROBE), 16384)
        if result.code or result.truncated:
            raise TransportError("Remote system probe failed")
        try:
            data = json.loads(result.output)
            if isinstance(data, dict):
                return data
        except (ValueError, RecursionError):
            pass
        raise TransportError("Malformed system metrics")


def resource_metrics(metrics):
    memory = metrics.get("memory")
    disk = metrics.get("disk")
    return {
        "cpu": metrics.get("cpu_percent"),
        "ram": memory.get("percent") if isinstance(memory, dict) else None,
        "disk": disk.get("percent") if isinstance(disk, dict) else None,
    }
