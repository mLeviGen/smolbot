# Architecture

```text
main.py / smolbot.__main__
  bot/                 Private-chat authorization, opaque buttons, views and routing
  core/hosts.py        Builds SNT and metrics providers from host configuration
  core/snt.py          Parses schema 1 and restricts CLI operations
  core/operations.py   Confirmation, serialization and result observation
  core/metrics.py      Local psutil and fixed SSH/Linux probe
  core/monitoring.py   Polls hosts, keeps views and detects transitions
  core/alerts.py       Delivers persisted events using current recipient preferences
  transports/          Bounded async local subprocesses and OpenSSH
  storage.py           SQLite schema, preferences, snapshots, event delivery and audit
```

Each enabled host gets one scheduled status call and one metrics probe per cycle. A semaphore caps concurrent hosts. A successful metrics probe keeps the host reachable when its SNT CLI fails. Latest successful SNT status remains available with its timestamp during outages. Manual refresh updates display data. Alert debounce counters advance on scheduled polls.

The first available SNT snapshot establishes a fresh baseline. Host outages require three consecutive failed scheduled observations by default. Resource alerts require sustained samples and report recovery. App transitions, restart counter increases within a daemon generation, observed running commit changes and new SNT errors produce separate events. Known in-flight SmolBot restarts and updates suppress expected process down/up alerts. CLI actions initiated outside SmolBot can look like process transitions.

The event and its pending recipients are written in the same SQLite transaction as the host snapshot. Cooldowns are keyed by host and event code. Delivery reevaluates the allowlist, mute and categories. Transient API failures leave pending rows for retry for up to an hour. A crash after successful Telegram send and before the SQLite acknowledgement can duplicate a notification. Preferences remain until removed by an operator. Events, audit rows, cooldown entries and unused snapshots are aged out. SQLite reuses pages after deletion.

An operation starts with a user-bound confirmation. One conflict guard covers each app until observation finishes. CLI submissions are serialized per host because Start and Stop modify a shared SNT configuration. A timed-out or interrupted write can have taken effect. SmolBot records uncertain outcomes and never replays commands on recovery. Run a single SmolBot instance per bot token and database. SNT 2.1.0 provides no distributed operation lock.

The deployment target is Python 3.12 on Linux. Standard-library SQLite runs in worker threads behind an async lock, which avoids an extra aiosqlite runtime dependency. Performance depends on host latency and alert volume. Use the workload and target measurements in [Testing](TESTING.md).
