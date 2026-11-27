import json
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values

APP_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
HOST_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Host:
    id: str
    name: str
    transport: str
    snt_path: str
    enabled: bool = True
    snt_home: str | None = None
    address: str = ""
    port: int = 22
    username: str = ""
    identity_file: str = ""
    known_hosts: str = "~/.ssh/known_hosts"
    connect_timeout: int = 10


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    users: frozenset[int]
    hosts: tuple[Host, ...]
    database: Path
    poll_interval: int
    command_timeout: int
    operation_timeout: int
    concurrent_hosts: int
    alerts_enabled: bool
    cooldown: int
    offline_samples: int
    resource_samples: int
    thresholds: dict[str, tuple[int, int]]
    retention_days: int
    log_level: str
    protected_apps: frozenset[str]
    redact_values: tuple[str, ...] = field(repr=False)


def resolve(value, base):
    return (base / Path(value).expanduser()).resolve()


def load_hosts(path, connect_timeout):
    try:
        entries = tomllib.loads(path.read_text(encoding="utf-8"))["hosts"]
    except (ValueError, OSError, KeyError):
        raise ConfigError("HOSTS_CONFIG must be readable TOML with [[hosts]] entries") from None
    if not isinstance(entries, list) or not entries:
        raise ConfigError("Configure at least one host")
    hosts = {}
    for index, entry in enumerate(entries, 1):
        error = f"Invalid host configuration at entry {index}"
        try:
            host = Host(**({"connect_timeout": connect_timeout} | entry))
        except TypeError:
            raise ConfigError(error) from None
        for item in fields(Host):
            value = getattr(host, item.name)
            if item.name in {"enabled", "port", "connect_timeout"}:
                continue
            if value is None and item.name == "snt_home":
                continue
            if not isinstance(value, str) or any(ord(c) < 32 for c in value):
                raise ConfigError(error)
        if (
            not HOST_ID.fullmatch(host.id)
            or not 1 <= len(host.name.strip()) <= 80
            or not host.snt_path
            or host.transport not in {"local", "ssh"}
            or type(host.enabled) is not bool
            or type(host.port) is not int
            or not 1 <= host.port <= 65535
            or type(host.connect_timeout) is not int
            or not 1 <= host.connect_timeout <= 60
        ):
            raise ConfigError(error)
        if host.id in hosts:
            raise ConfigError(f"Duplicate host ID at entry {index}")
        if host.transport == "ssh":
            if (
                not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9.-]*|[0-9A-Fa-f:]*:[0-9A-Fa-f:]+", host.address
                )
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", host.username)
                or not host.identity_file
                or not host.known_hosts
                or not PurePosixPath(host.snt_path).is_absolute()
                or (host.snt_home and not PurePosixPath(host.snt_home).is_absolute())
            ):
                raise ConfigError(error)
            host = replace(
                host,
                **{
                    key: str(resolve(getattr(host, key), path.parent))
                    for key in ("identity_file", "known_hosts")
                },
            )
        else:
            host = replace(
                host,
                **{
                    key: str(resolve(value, path.parent))
                    for key in ("snt_path", "snt_home")
                    if (value := getattr(host, key))
                    and (key == "snt_home" or "/" in value or "\\" in value)
                },
            )
        hosts[host.id] = host
    return tuple(hosts.values())


def load_settings(environment=None, *, cwd=None):
    environment = os.environ if environment is None else environment
    cwd = Path.cwd() if cwd is None else cwd
    env_file = resolve(environment.get("SMOLBOT_ENV_FILE", ".env"), cwd)
    if "SMOLBOT_ENV_FILE" in environment and not env_file.is_file():
        raise ConfigError("SMOLBOT_ENV_FILE is not readable")
    try:
        values = dotenv_values(env_file, interpolate=False, encoding="utf-8") | dict(environment)
    except (OSError, ValueError):
        raise ConfigError("Could not read environment configuration") from None

    def value(name, default=""):
        return values.get(name) or default

    def integer(name, default, minimum, maximum):
        try:
            number = int(value(name, str(default)))
            if minimum <= number <= maximum:
                return number
        except ValueError:
            pass
        raise ConfigError(f"{name} must be an integer between {minimum} and {maximum}")

    token = value("BOT_TOKEN")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ConfigError("BOT_TOKEN is missing or malformed")
    raw_ids = value("ALLOWED_USER_IDS").strip()
    ids = [part.strip() for part in raw_ids.split(",")] if raw_ids else []
    if not ids:
        raise ConfigError("ALLOWED_USER_IDS is empty; access is denied")
    if any(not re.fullmatch(r"[1-9][0-9]{0,15}", part) or int(part) >= 2**52 for part in ids):
        raise ConfigError("ALLOWED_USER_IDS must contain comma-separated positive Telegram IDs")
    alerts = value("ALERTS_ENABLED", "true").lower()
    if alerts not in {"true", "false"}:
        raise ConfigError("ALERTS_ENABLED must be true or false")
    level = value("LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError("Invalid LOG_LEVEL")
    protected = {"smolbot"} | {
        name.strip().casefold() for name in value("PROTECTED_APP_NAMES").split(",") if name.strip()
    }
    if any(not APP_NAME.fullmatch(name) for name in protected):
        raise ConfigError("Invalid PROTECTED_APP_NAMES")
    try:
        redact = json.loads(value("LOG_REDACT_VALUES", "[]"))
        if not isinstance(redact, list) or any(not isinstance(x, str) or not x for x in redact):
            raise ValueError
    except ValueError:
        raise ConfigError("LOG_REDACT_VALUES must be a JSON list of nonempty strings") from None
    thresholds = {
        metric.lower(): (
            integer(f"{metric}_WARNING", warning, 1, 99),
            integer(f"{metric}_CRITICAL", critical, 2, 100),
        )
        for metric, warning, critical in (("CPU", 85, 95), ("RAM", 80, 90), ("DISK", 80, 90))
    }
    if any(w >= c for w, c in thresholds.values()):
        raise ConfigError("Warning thresholds must be lower than critical thresholds")
    return Settings(
        token,
        frozenset(map(int, ids)),
        load_hosts(
            resolve(value("HOSTS_CONFIG", "hosts.toml"), env_file.parent),
            integer("SSH_CONNECT_TIMEOUT_SECONDS", 10, 1, 60),
        ),
        resolve(value("DATABASE_PATH", "data/smolbot.db"), env_file.parent),
        integer("POLL_INTERVAL_SECONDS", 30, 10, 300),
        integer("COMMAND_TIMEOUT_SECONDS", 35, 1, 300),
        integer("OPERATION_TIMEOUT_SECONDS", 90, 5, 600),
        integer("MAX_CONCURRENT_HOSTS", 4, 1, 32),
        alerts == "true",
        integer("ALERT_COOLDOWN_SECONDS", 300, 0, 86400),
        integer("OFFLINE_SAMPLES", 3, 3, 20),
        integer("RESOURCE_SAMPLES", 3, 2, 20),
        thresholds,
        integer("EVENT_RETENTION_DAYS", 30, 1, 365),
        level,
        frozenset(protected),
        (token, *redact),
    )
